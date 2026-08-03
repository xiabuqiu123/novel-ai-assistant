import 'dart:async';

import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

import 'shared_widgets.dart';

class QaScreen extends StatefulWidget {
  const QaScreen({super.key, required this.apiClient, required this.novel});

  final NovelApiClient apiClient;
  final Novel novel;

  @override
  State<QaScreen> createState() => _QaScreenState();
}

class _QaScreenState extends State<QaScreen> {
  static const _qaPollTimeout = Duration(minutes: 5);

  final _questionController = TextEditingController();
  bool _isAsking = false;
  bool _cancelRequested = false;
  bool _forceRefresh = false;
  int? _qaJobId;
  String? _qaJobStatus;
  Duration _qaElapsed = Duration.zero;
  QaResult? _result;
  String? _error;
  final List<_QaHistory> _history = [];

  @override
  void dispose() {
    _questionController.dispose();
    super.dispose();
  }

  Future<void> _ask() async {
    final question = _questionController.text.trim();
    if (question.isEmpty) return;
    setState(() {
      _isAsking = true;
      _cancelRequested = false;
      _error = null;
      _result = null;
      _qaJobId = null;
      _qaJobStatus = null;
      _qaElapsed = Duration.zero;
    });
    try {
      final startResult = await widget.apiClient.startQa(
        novelId: widget.novel.id,
        question: question,
        forceRefresh: _forceRefresh,
      );
      if (!mounted) return;
      setState(() {
        _qaJobId = startResult.jobId;
        _qaJobStatus = startResult.status;
      });
      QaResult? result;
      final qaStart = DateTime.now();
      while (true) {
        if (_cancelRequested) {
          throw Exception('已取消问答任务 #${startResult.jobId}');
        }
        if (DateTime.now().difference(qaStart) > _qaPollTimeout) {
          throw Exception('等待回答超时（${_qaPollTimeout.inMinutes} 分钟），任务仍在后台运行，请稍后在分析任务页查看');
        }
        await Future<void>.delayed(const Duration(seconds: 2));
        final job = await widget.apiClient.getAnalysisJob(startResult.jobId);
        if (!mounted) return;
        setState(() {
          _qaJobStatus = job.status;
          _qaElapsed = DateTime.now().difference(qaStart);
        });
        if (job.status == 'completed') {
          final jobResult = await widget.apiClient.getJobResult(startResult.jobId);
          final merged = jobResult.mergedResult();
          if (merged == null) throw Exception('问答任务已完成但未返回结果');
          result = QaResult.fromJson(merged);
          break;
        }
        if (job.status == 'failed') {
          throw Exception(job.error.isNotEmpty ? job.error : '问答任务失败');
        }
      }
      final completedResult = result;
      setState(() {
        _result = completedResult;
        _history.insert(0, _QaHistory(question: question, result: completedResult));
        _questionController.clear();
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.toString();
      });
    } finally {
      if (mounted) {
        setState(() {
          _isAsking = false;
        });
      }
    }
  }

  Future<void> _cancelQa() async {
    final jobId = _qaJobId;
    if (jobId == null || _cancelRequested) return;
    setState(() => _cancelRequested = true);
    try {
      await widget.apiClient.cancelAnalysisJob(jobId);
    } catch (_) {
      // 取消失败（任务可能已结束）时仍停止本地等待。
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('AI 问答: ${widget.novel.title}')),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: Column(
              children: [
                Expanded(
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      if (_isAsking && _qaJobId != null) ...[
                        Card(
                          child: ListTile(
                            leading: const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
                            title: Text('问答任务 #$_qaJobId: ${_qaJobStatus ?? 'queued'}'),
                            subtitle: Text('已等待 ${_qaElapsed.inSeconds} 秒 · 任务在后台运行，可切换页面，回来自动续看'),
                            trailing: TextButton(
                              key: const Key('qa-cancel-button'),
                              onPressed: _cancelRequested ? null : _cancelQa,
                              child: const Text('取消'),
                            ),
                          ),
                        ),
                        const SizedBox(height: 12),
                      ],
                      if (_error != null)
                        ErrorState(message: _error!, onRetry: _ask),
                      if (_result != null) ...[
                        _QaAnswerCard(result: _result!),
                        const SizedBox(height: 16),
                      ],
                      if (_history.isNotEmpty) ...[
                        const Divider(),
                        Text('历史记录', style: Theme.of(context).textTheme.titleMedium),
                        const SizedBox(height: 8),
                        ..._history.map((h) => _QaHistoryTile(history: h)),
                      ],
                    ],
                  ),
                ),
                _QaInputBar(
                  controller: _questionController,
                  isLoading: _isAsking,
                  forceRefresh: _forceRefresh,
                  onForceRefreshChanged: (value) {
                    setState(() {
                      _forceRefresh = value;
                    });
                  },
                  onSubmit: _ask,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _QaHistory {
  _QaHistory({required this.question, required this.result});
  final String question;
  final QaResult result;
}

class _QaHistoryTile extends StatelessWidget {
  const _QaHistoryTile({required this.history});
  final _QaHistory history;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        title: Text(history.question, maxLines: 2, overflow: TextOverflow.ellipsis),
        subtitle: Text(
          history.result.answer,
          maxLines: 3,
          overflow: TextOverflow.ellipsis,
        ),
        isThreeLine: true,
      ),
    );
  }
}

class _QaAnswerCard extends StatelessWidget {
  const _QaAnswerCard({required this.result});
  final QaResult result;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('回答', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            SelectableText(result.answer.isEmpty ? '(未返回答案)' : result.answer),
            if (result.reasoning.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text('推理过程', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 4),
              Text(result.reasoning),
            ],
            if (result.uncertainty.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text('不确定性', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 4),
              Text(result.uncertainty),
            ],
            if (result.needsMoreContext) ...[
              const SizedBox(height: 12),
              const Chip(label: Text('需要更多上下文')),
            ],
            if (result.evidence.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text('证据(共 ${result.evidence.length} 条)', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 4),
              ...result.evidence.take(5).map((e) => _QaEvidenceTile(evidence: e)),
            ],
            const SizedBox(height: 8),
            ProvenancePanel(status: result.status, provenance: result.provenance),
          ],
        ),
      ),
    );
  }
}

class _QaEvidenceTile extends StatelessWidget {
  const _QaEvidenceTile({required this.evidence});
  final QaEvidence evidence;

  @override
  Widget build(BuildContext context) {
    final label = evidence.chapterTitle.isNotEmpty
        ? '第 ${evidence.chapterOrder > 0 ? evidence.chapterOrder : evidence.chapterId} 章: ${evidence.chapterTitle}'
        : '第 ${evidence.chapterOrder > 0 ? evidence.chapterOrder : evidence.chapterId} 章';
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: Theme.of(context).textTheme.bodySmall),
          if (evidence.quote.isNotEmpty)
            Text(
              evidence.quote,
              maxLines: 4,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
        ],
      ),
    );
  }
}

class _QaInputBar extends StatelessWidget {
  const _QaInputBar({
    required this.controller,
    required this.isLoading,
    required this.forceRefresh,
    required this.onForceRefreshChanged,
    required this.onSubmit,
  });

  final TextEditingController controller;
  final bool isLoading;
  final bool forceRefresh;
  final ValueChanged<bool> onForceRefreshChanged;
  final VoidCallback onSubmit;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        border: Border(
          top: BorderSide(color: Theme.of(context).dividerColor),
        ),
      ),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: controller,
              decoration: const InputDecoration(
                hintText: '就这本小说提问…',
                border: OutlineInputBorder(),
                contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              ),
              enabled: !isLoading,
              onSubmitted: (_) => onSubmit(),
            ),
          ),
          const SizedBox(width: 8),
          Tooltip(
            message: '绕过缓存的问答结果',
            child: FilterChip(
              key: const Key('qa-force-refresh-chip'),
              label: const Text('强制刷新'),
              selected: forceRefresh,
              onSelected: isLoading ? null : onForceRefreshChanged,
            ),
          ),
          const SizedBox(width: 8),
          isLoading
              ? const SizedBox(
                  width: 24,
                  height: 24,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : IconButton.filled(
                  onPressed: onSubmit,
                  icon: const Icon(Icons.send),
                ),
        ],
      ),
    );
  }
}
