import 'dart:async';

import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

import 'shared_widgets.dart';

class AnalysisJobsScreen extends StatefulWidget {
  const AnalysisJobsScreen({super.key, required this.apiClient, this.novelId});

  final NovelApiClient apiClient;
  final int? novelId;

  @override
  State<AnalysisJobsScreen> createState() => _AnalysisJobsScreenState();
}

class _AnalysisJobsScreenState extends State<AnalysisJobsScreen> {
  // Held in state instead of a FutureBuilder: re-creating a Future every poll
  // tick made the FutureBuilder rebuild into ConnectionState.waiting and flash a
  // full-screen spinner. Now we only rebuild when something actually changed.
  List<AnalysisJob>? _jobs;
  Object? _error;
  Timer? _refreshTimer;
  bool _runningNext = false;
  bool _showAllJobs = false;
  String _lastSignature = '';

  @override
  void initState() {
    super.initState();
    _refresh(force: true);
    _refreshTimer = Timer.periodic(const Duration(seconds: 2), (_) => _refresh());
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  // Only id+status+progress drive what the user sees; comparing this string lets
  // the 2s poll stay silent when nothing changed.
  String _signature(List<AnalysisJob> jobs) {
    return jobs.map((job) => '${job.id}:${job.status}:${job.progress}').join('|');
  }

  Future<void> _refresh({bool force = false}) async {
    List<AnalysisJob> jobs;
    try {
      jobs = await widget.apiClient.listAnalysisJobs(novelId: widget.novelId);
    } catch (e) {
      if (!mounted) return;
      // Only the very first load may surface the error screen; later poll
      // failures keep the last known list so the UI never flashes.
      if (_jobs == null) {
        setState(() {
          _error = e;
        });
      }
      return;
    }
    if (!mounted) return;
    final signature = _signature(jobs);
    if (force || _jobs == null || signature != _lastSignature) {
      setState(() {
        _jobs = jobs;
        _error = null;
        _lastSignature = signature;
      });
    }
  }

  void _retry() {
    _refresh(force: true);
  }

  Future<void> _retryJob(int jobId) async {
    try {
      await widget.apiClient.retryAnalysisJob(jobId);
      await widget.apiClient.runAnalysisJob(jobId);
    } catch (_) {
      // The refreshed jobs list stays the source of truth.
    }
    if (!mounted) return;
    _retry();
  }

  Future<void> _cancelJob(int jobId) async {
    try {
      await widget.apiClient.cancelAnalysisJob(jobId);
    } catch (_) {
      // Keep the jobs list as the source of truth; refresh below.
    }
    if (!mounted) return;
    _retry();
  }

  Future<void> _runNextJob() async {
    if (_runningNext) return;
    setState(() => _runningNext = true);
    try {
      await widget.apiClient.runNextAnalysisJob();
    } catch (_) {
      // Keep the jobs list as the source of truth; refresh below.
    }
    if (!mounted) return;
    setState(() => _runningNext = false);
    _retry();
  }

  @override
  Widget build(BuildContext context) {
    final jobs = _jobs;
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.novelId != null ? '分析任务' : '全部分析任务'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _retry,
          ),
          IconButton(
            tooltip: '运行排队任务',
            icon: _runningNext
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.play_arrow),
            onPressed: _runningNext ? null : _runNextJob,
          ),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: _buildBody(context, jobs),
          ),
        ),
      ),
    );
  }

  Widget _buildBody(BuildContext context, List<AnalysisJob>? jobs) {
    if (jobs == null) {
      if (_error != null) {
        return ErrorState(message: _error.toString(), onRetry: _retry);
      }
      return const Center(child: CircularProgressIndicator());
    }
    if (jobs.isEmpty) {
      return const Center(
        child: Text('暂无分析任务, 请先导入小说并运行分析。'),
      );
    }
    // 按更新时间倒序展示，避免任务列表随时间无限增长后刷屏。
    final sorted = [...jobs]
      ..sort((a, b) {
        final cmp = b.updatedAt.compareTo(a.updatedAt);
        if (cmp != 0) return cmp;
        return b.id.compareTo(a.id);
      });
    const previewLimit = 50;
    final truncated = !_showAllJobs && sorted.length > previewLimit;
    final visible = truncated ? sorted.sublist(0, previewLimit) : sorted;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text('共 ${sorted.length} 个任务${truncated ? '（仅显示最近 $previewLimit 条）' : ''}',
            style: Theme.of(context).textTheme.bodyMedium),
        if (sorted.length > previewLimit)
          Padding(
            padding: const EdgeInsets.only(top: 4, bottom: 8),
            child: Center(
              child: TextButton(
                onPressed: () => setState(() => _showAllJobs = !_showAllJobs),
                child: Text(_showAllJobs ? '只看最近 $previewLimit 条' : '显示全部（${sorted.length} 条）'),
              ),
            ),
          ),
        const SizedBox(height: 8),
        ...visible.map((job) => _JobCard(
              job: job,
              onRetry: () => _retryJob(job.id),
              onCancel: () => _cancelJob(job.id),
            )),
      ],
    );
  }
}

class _JobCard extends StatelessWidget {
  const _JobCard({required this.job, required this.onRetry, required this.onCancel});
  final AnalysisJob job;
  final VoidCallback onRetry;
  final VoidCallback onCancel;

  @override
  Widget build(BuildContext context) {
    final statusColor = switch (job.status) {
      'completed' => Colors.green,
      'failed' => Colors.red,
      'running' => Colors.blue,
      'cancelled' => Colors.orange,
      _ => Colors.grey,
    };

    final statusIcon = switch (job.status) {
      'completed' => Icons.check_circle_outline,
      'failed' => Icons.error_outline,
      'running' => Icons.hourglass_top,
      'queued' => Icons.schedule,
      'cancelled' => Icons.cancel_outlined,
      _ => Icons.help_outline,
    };

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(statusIcon, color: statusColor, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '任务 #${job.id} - ${job.taskType}',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                ),
                Chip(
                  label: Text(displayJobStatus(job.status), style: TextStyle(fontSize: 11, color: statusColor)),
                  backgroundColor: statusColor.withAlpha(20),
                  side: BorderSide.none,
                  padding: EdgeInsets.zero,
                  materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
              ],
            ),
            if (job.status == 'running' || job.status == 'queued') ...[
              const SizedBox(height: 8),
              LinearProgressIndicator(value: job.progress / 100),
              const SizedBox(height: 4),
              Text('${job.progress}%', style: Theme.of(context).textTheme.bodySmall),
            ],
            if (job.error.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                job.error,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(color: Colors.red),
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
              ),
            ],
            if (job.novelId != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  '小说: ${job.novelId}  |  重试次数: ${job.retryCount}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '模型: ${job.effectiveModel.isEmpty ? '未知' : job.effectiveModel}  |  来源: ${job.cacheSource.isEmpty ? '无' : job.cacheSource}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
            if (job.cacheSource == 'cached_partial') ...[
              const SizedBox(height: 4),
              Text(
                '部分成功（有批次走了本地兜底，建议重跑）',
                style: Theme.of(context).textTheme.bodySmall?.copyWith(color: Colors.orange),
              ),
            ],
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '服务商调用: ${job.providerCallAttempted ? '已尝试' : '未尝试'}  |  成功: ${job.providerCallSucceeded ? '是' : '否'}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
            if (job.modelError.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(
                '模型错误: ${job.modelError}',
                style: Theme.of(context).textTheme.bodySmall?.copyWith(color: Colors.red),
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
              ),
            ],
            if (job.resultCacheKey.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  '缓存: ${job.resultCacheKey}',
                  style: Theme.of(context).textTheme.bodySmall,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            if (job.status == 'queued' || job.status == 'running') ...[
              const SizedBox(height: 8),
              OutlinedButton.icon(
                key: Key('cancel-job-${job.id}'),
                onPressed: onCancel,
                icon: const Icon(Icons.stop_outlined, size: 16),
                label: const Text('取消'),
              ),
            ],
            if (job.status == 'failed') ...[
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.replay, size: 16),
                label: const Text('重试'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
