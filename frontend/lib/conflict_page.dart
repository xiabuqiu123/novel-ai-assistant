import 'dart:async';

import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

/// 设定冲突检测（PRD 4.10）含人工复核工作流。
class ConflictDetectionScreen extends StatefulWidget {
  const ConflictDetectionScreen({super.key, required this.apiClient, required this.novel});

  final NovelApiClient apiClient;
  final Novel novel;

  @override
  State<ConflictDetectionScreen> createState() => _ConflictDetectionScreenState();
}

class _ConflictDetectionScreenState extends State<ConflictDetectionScreen> {
  static const _reviewStatuses = <String>['pending_review', 'confirmed', 'dismissed', 'explained', 'watching'];
  static const _statusLabels = <String, String>{
    'pending_review': '待复核',
    'confirmed': '已确认',
    'dismissed': '已忽略',
    'explained': '已解释',
    'watching': '持续观察',
  };
  static const _severityColors = <String, Color>{
    'high': Colors.red,
    'medium': Colors.orange,
    'low': Colors.blueGrey,
  };

  Future<List<ExtractedFact>>? _conflictsFuture;
  bool _isStarting = false;
  int? _jobId;
  String? _jobStatus;
  int? _jobProgress;
  String? _jobError;
  Timer? _pollTimer;

  @override
  void initState() {
    super.initState();
    _conflictsFuture = _load();
  }

  Future<List<ExtractedFact>> _load() async {
    final conflicts = await widget.apiClient.listFacts(novelId: widget.novel.id, factType: 'setting_conflict');
    // E4: superseded facts belong to an older run; only show live facts.
    return conflicts.where((conflict) => conflict.status != 'superseded').toList();
  }

  void _retry() {
    setState(() { _conflictsFuture = _load(); });
  }

  Future<void> _startDetection() async {
    setState(() {
      _isStarting = true;
      _jobError = null;
    });
    try {
      final result = await widget.apiClient.startConflicts(novelId: widget.novel.id);
      setState(() {
        _jobId = result.jobId;
        _jobStatus = result.status;
        _jobProgress = 0;
      });
      _startPolling();
    } catch (error) {
      setState(() => _jobError = error.toString());
    } finally {
      setState(() => _isStarting = false);
    }
  }

  void _startPolling() {
    _pollTimer?.cancel();
    _pollTimer = Timer.periodic(const Duration(seconds: 1), (timer) async {
      if (_jobId == null) {
        timer.cancel();
        return;
      }
      try {
        final job = await widget.apiClient.getAnalysisJob(_jobId!);
        setState(() {
          _jobStatus = job.status;
          _jobProgress = job.progress;
          _jobError = job.error.isEmpty ? null : job.error;
        });
        if (job.status == 'completed' || job.status == 'failed' || job.status == 'cancelled') {
          timer.cancel();
          _retry();
        }
      } catch (error) {
        timer.cancel();
        setState(() => _jobError = error.toString());
      }
    });
  }

  Future<void> _updateReview(ExtractedFact conflict, String status) async {
    try {
      await widget.apiClient.updateReviewStatus(
        recordType: 'extracted_fact',
        recordId: conflict.id,
        status: status,
      );
      _retry();
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('更新失败：$error')));
      }
    }
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('设定冲突检测')),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: FilledButton.icon(
                    onPressed: _isStarting ? null : _startDetection,
                    icon: const Icon(Icons.report_problem_outlined),
                    label: Text(_isStarting ? '检测中…' : '检测设定冲突'),
                  ),
                ),
                if (_jobStatus != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Row(
                      children: [
                        Expanded(
                          child: LinearProgressIndicator(
                            value: (_jobProgress ?? 0) / 100.0,
                            semanticsLabel: '检测进度',
                          ),
                        ),
                        const SizedBox(width: 12),
                        Text(_jobStatus ?? ''),
                      ],
                    ),
                  ),
                if (_jobError != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                    child: Text('错误：$_jobError',
                        style: TextStyle(color: Theme.of(context).colorScheme.error)),
                  ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                  child: Text(
                    '冲突检测结果为疑似问题，需人工复核后才会作为最终结论。',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
                Expanded(
                  child: FutureBuilder<List<ExtractedFact>>(
                    future: _conflictsFuture,
                    builder: (context, snapshot) {
                      if (snapshot.connectionState == ConnectionState.waiting) {
                        return const Center(child: CircularProgressIndicator());
                      }
                      if (snapshot.hasError) {
                        return RefreshIndicator(
                          onRefresh: () async => _retry(),
                          child: ListView(
                            physics: const AlwaysScrollableScrollPhysics(),
                            children: [Center(child: Text('加载失败：${snapshot.error}'))],
                          ),
                        );
                      }
                      final conflicts = snapshot.data ?? <ExtractedFact>[];
                      if (conflicts.isEmpty) {
                        return Center(
                          child: Text('暂无冲突，需先抽取人物/设定后再检测。',
                              style: Theme.of(context).textTheme.bodyMedium),
                        );
                      }
                      return RefreshIndicator(
                        onRefresh: () async => _retry(),
                        child: ListView.builder(
                          physics: const AlwaysScrollableScrollPhysics(),
                          padding: const EdgeInsets.all(16),
                          itemCount: conflicts.length,
                          itemBuilder: (context, index) => _conflictCard(conflicts[index]),
                        ),
                      );
                    },
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _conflictCard(ExtractedFact conflict) {
    final severity = conflict.extra['severity']?.toString() ?? 'low';
    final color = _severityColors[severity] ?? Colors.blueGrey;
    final earlier = (conflict.extra['earlier_evidence'] as List?) ?? const [];
    final later = (conflict.extra['later_evidence'] as List?) ?? const [];
    final explanation = conflict.extra['possible_explanation']?.toString() ?? '';
    final judgment = conflict.extra['model_judgment']?.toString() ?? '';
    final type = conflict.extra['type']?.toString() ?? '';
    final explanationEvidence = (conflict.extra['explanation_evidence'] as List?) ?? const [];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.warning_amber, color: color),
                const SizedBox(width: 8),
                Expanded(child: Text(conflict.content, style: Theme.of(context).textTheme.titleMedium)),
                Chip(
                  label: Text(severity),
                  backgroundColor: color.withValues(alpha: 0.15),
                ),
                const SizedBox(width: 8),
                Chip(label: Text(_statusLabels[conflict.status] ?? conflict.status)),
              ],
            ),
            if (conflict.entities.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('涉及实体：${conflict.entities.join('，')}'),
            ],
            if (earlier.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('较早证据', style: Theme.of(context).textTheme.labelMedium),
              for (final item in earlier) Text('· ${item['source_quote'] ?? ''}'),
            ],
            if (later.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('较晚证据', style: Theme.of(context).textTheme.labelMedium),
              for (final item in later) Text('· ${item['source_quote'] ?? ''}'),
            ],
            if (explanation.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('可能解释：$explanation'),
            ],
            if (judgment.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text('模型判断：$judgment'),
            ],
            if (type.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text('类型：$type', style: Theme.of(context).textTheme.labelMedium),
            ],
            if (explanationEvidence.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('相关解释证据', style: Theme.of(context).textTheme.labelMedium),
              for (final item in explanationEvidence) Text('· ${item['source_quote'] ?? ''}'),
            ],
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              children: [
                for (final status in _reviewStatuses)
                  ActionChip(
                    label: Text(_statusLabels[status] ?? status),
                    onPressed: conflict.status == status ? null : () => _updateReview(conflict, status),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
