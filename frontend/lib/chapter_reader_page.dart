import 'dart:async';

import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

import 'shared_widgets.dart';

class ChapterReaderScreen extends StatefulWidget {
  const ChapterReaderScreen({super.key, required this.apiClient, required this.chapterSummary});

  final NovelApiClient apiClient;
  final ChapterSummary chapterSummary;

  @override
  State<ChapterReaderScreen> createState() => _ChapterReaderScreenState();
}

class _ChapterReaderScreenState extends State<ChapterReaderScreen> {
  Future<Chapter>? _chapterFuture;
  bool _isSummarizing = false;
  ChapterSummaryResult? _summaryResult;
  String? _summaryError;

  @override
  void initState() {
    super.initState();
    _chapterFuture = _load();
  }

  Future<Chapter> _load() {
    return widget.apiClient.getChapter(widget.chapterSummary.id);
  }

  void _retry() {
    setState(() {
      _chapterFuture = _load();
    });
  }

  int? _summaryJobId;
  String? _summaryJobStatus;
  int _summaryRunToken = 0;

  Future<void> _summarize({bool forceRefresh = false}) async {
    final token = ++_summaryRunToken;
    setState(() {
      _isSummarizing = true;
      _summaryError = null;
      _summaryResult = null;
      _summaryJobId = null;
      _summaryJobStatus = null;
    });
    try {
      final startResult = await widget.apiClient.startChapterSummary(
        widget.chapterSummary.id,
        forceRefresh: forceRefresh,
      );
      if (!mounted || token != _summaryRunToken) return;
      setState(() {
        _summaryJobId = startResult.jobId;
        _summaryJobStatus = startResult.status;
      });
      await _pollSummaryJob(startResult.jobId, token);
    } catch (error) {
      if (!mounted || token != _summaryRunToken) return;
      setState(() {
        _summaryError = error.toString();
        _isSummarizing = false;
      });
    }
  }

  Future<void> _pollSummaryJob(int jobId, int token) async {
    while (true) {
      if (!mounted || token != _summaryRunToken) return;
      try {
        final job = await widget.apiClient.getAnalysisJob(jobId);
        if (!mounted || token != _summaryRunToken) return;
        setState(() {
          _summaryJobStatus = job.status;
        });
        if (job.status == 'completed' || job.status == 'failed') {
          final jobResult = await widget.apiClient.getJobResult(jobId);
          if (!mounted || token != _summaryRunToken) return;
          final merged = jobResult.mergedResult();
          if (merged != null) {
            setState(() {
              _summaryResult = ChapterSummaryResult.fromJson(merged);
              _isSummarizing = false;
            });
            return;
          }
          if (job.status == 'failed') {
            setState(() {
              _summaryError = job.error.isNotEmpty ? job.error : '任务失败';
              _isSummarizing = false;
            });
            return;
          }
        }
        await Future<void>.delayed(const Duration(seconds: 2));
      } catch (error) {
        if (!mounted || token != _summaryRunToken) return;
        setState(() {
          _summaryError = error.toString();
          _isSummarizing = false;
        });
        return;
      }
    }
  }

  @override
  void dispose() {
    _summaryRunToken++;
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.chapterSummary.title)),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: FutureBuilder<Chapter>(
              future: _chapterFuture,
              builder: (context, snapshot) {
                final isLoading = snapshot.connectionState == ConnectionState.waiting;
                final error = snapshot.error;
                final chapter = snapshot.data;

                return ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    if (isLoading)
                      const LinearProgressIndicator()
                    else if (error != null)
                      ErrorState(message: error.toString(), onRetry: _retry)
                    else if (chapter != null) ...[
                      Text(chapter.title, style: Theme.of(context).textTheme.titleLarge),
                      const SizedBox(height: 4),
                      Text(
                        '第 ${chapter.order} 章 · 共 ${chapter.content.length} 字',
                        style: Theme.of(context).textTheme.bodyMedium,
                      ),
                      const SizedBox(height: 16),
                      FilledButton.icon(
                        key: const Key('summarize-chapter-button'),
                        onPressed: _isSummarizing ? null : () => _summarize(),
                        icon: _isSummarizing
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.auto_awesome_outlined),
                        label: Text(_isSummarizing ? '摘要生成中' : '生成本章摘要'),
                      ),
                      const SizedBox(height: 8),
                      OutlinedButton.icon(
                        key: const Key('force-refresh-chapter-summary-button'),
                        onPressed: _isSummarizing ? null : () => _summarize(forceRefresh: true),
                        icon: const Icon(Icons.refresh),
                        label: const Text('强制刷新摘要'),
                      ),
                      if (_isSummarizing && _summaryJobId != null) ...[
                        const SizedBox(height: 8),
                        Text(
                          '摘要任务 #$_summaryJobId | 状态: ${_summaryJobStatus ?? 'queued'}',
                          key: const Key('chapter-summary-job-status'),
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                        const SizedBox(height: 4),
                        Text(
                          '任务在后台运行，可切换页面，回来自动续看',
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ],                      if (_summaryError != null) ...[
                        const SizedBox(height: 16),
                        ErrorState(message: _summaryError!, onRetry: () => _summarize()),
                      ],
                      if (_summaryResult != null) ...[
                        const SizedBox(height: 16),
                        ChapterSummaryResultPanel(result: _summaryResult!),
                      ],
                      const SizedBox(height: 16),
                      SelectableText(
                        chapter.content,
                        style: Theme.of(context).textTheme.bodyLarge?.copyWith(height: 1.6),
                      ),
                    ],
                  ],
                );
              },
            ),
          ),
        ),
      ),
    );
  }
}
