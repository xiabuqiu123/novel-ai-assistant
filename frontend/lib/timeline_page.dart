import 'dart:async';

import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

/// 事件时间线页（PRD 4.9）。
class TimelineScreen extends StatefulWidget {
  const TimelineScreen({super.key, required this.apiClient, required this.novel});

  final NovelApiClient apiClient;
  final Novel novel;

  @override
  State<TimelineScreen> createState() => _TimelineScreenState();
}

class _TimelineScreenState extends State<TimelineScreen> {
  Future<List<ExtractedFact>>? _eventsFuture;
  bool _isStarting = false;
  int? _jobId;
  String? _jobStatus;
  int? _jobProgress;
  String? _jobError;
  Timer? _pollTimer;

  @override
  void initState() {
    super.initState();
    _eventsFuture = _load();
  }

  Future<List<ExtractedFact>> _load() async {
    final events = await widget.apiClient.listFacts(novelId: widget.novel.id, factType: 'event');
    // E4: superseded facts belong to an older run; only show live facts.
    return events.where((ev) => ev.status != 'superseded').toList();
  }

  void _retry() {
    setState(() {
      _eventsFuture = _load();
    });
  }

  Future<void> _startExtraction({bool forceRefresh = false}) async {
    setState(() {
      _isStarting = true;
      _jobError = null;
    });
    try {
      final result = await widget.apiClient.startEvents(
        novelId: widget.novel.id,
        forceRefresh: forceRefresh,
      );
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

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  /// D1：先按故事时代（era）分组，组内按 story_time_order 排序；era 或
  /// story_time_order 缺失的事件无法判断时序，归入「时序不明」组并排在最后。
  List<_EraGroup> _grouped(List<ExtractedFact> events) {
    final sorted = [...events]
      ..sort((a, b) {
        final hasA = _hasStoryTime(a);
        final hasB = _hasStoryTime(b);
        // 可判断时序的事件在前，「时序不明」组排在最后。
        if (hasA != hasB) return hasA ? -1 : 1;
        final cmp = _storyOrder(a).compareTo(_storyOrder(b));
        if (cmp != 0) return cmp;
        final chapterCmp = _chapterOrder(a).compareTo(_chapterOrder(b));
        if (chapterCmp != 0) return chapterCmp;
        return a.id.compareTo(b.id);
      });
    final groups = <_EraGroup>[];
    final index = <String, int>{};
    for (final ev in sorted) {
      final era = _era(ev);
      final key = era.isNotEmpty ? 'era:$era' : 'unknown';
      final pos = index[key];
      if (pos == null) {
        index[key] = groups.length;
        groups.add(_EraGroup(era: era, events: [ev]));
      } else {
        groups[pos].events.add(ev);
      }
    }
    return groups;
  }

  String _era(ExtractedFact ev) => (ev.extra['era']?.toString() ?? '').trim();
  int _storyOrder(ExtractedFact ev) =>
      (ev.extra['story_time_order'] as num?)?.toInt() ?? 0;
  int _chapterOrder(ExtractedFact ev) =>
      (ev.extra['chapter_order'] as num?)?.toInt() ?? 0;
  bool _hasStoryTime(ExtractedFact ev) => _era(ev).isNotEmpty && _storyOrder(ev) > 0;

  String _groupHeader(_EraGroup group) {
    if (group.era.isEmpty) {
      return '时序不明（AI 推断时序，供参考）';
    }
    return group.era;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('事件时间线')),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      FilledButton.icon(
                        onPressed: _isStarting ? null : () => _startExtraction(),
                        icon: const Icon(Icons.timeline_outlined),
                        label: Text(_isStarting ? '抽取中…' : '一键抽取时间线'),
                      ),
                      OutlinedButton.icon(
                        key: const Key('re-extract-events-button'),
                        onPressed: _isStarting ? null : () => _startExtraction(forceRefresh: true),
                        icon: const Icon(Icons.refresh),
                        label: const Text('重新抽取事件'),
                      ),
                    ],
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
                            semanticsLabel: '抽取进度',
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
                Expanded(
                  child: FutureBuilder<List<ExtractedFact>>(
                    future: _eventsFuture,
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
                      final events = snapshot.data ?? <ExtractedFact>[];
                      if (events.isEmpty) {
                        return Center(
                          child: Text('暂无时间线数据，点击「一键抽取时间线」开始。',
                              style: Theme.of(context).textTheme.bodyMedium),
                        );
                      }
                      final groups = _grouped(events);
                      return Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Container(
                            margin: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                            decoration: BoxDecoration(
                              color: Theme.of(context).colorScheme.surfaceContainerHighest,
                              borderRadius: BorderRadius.circular(6),
                            ),
                            child: const Text(
                              '事件时序为 AI 推断，仅供参考（插叙/多线叙事可能不准）。',
                              style: TextStyle(fontSize: 12),
                            ),
                          ),
                          Expanded(
                            child: RefreshIndicator(
                              onRefresh: () async => _retry(),
                              child: ListView.builder(
                                physics: const AlwaysScrollableScrollPhysics(),
                                padding: const EdgeInsets.all(16),
                                itemCount: groups.length,
                                itemBuilder: (context, index) {
                                  final group = groups[index];
                                  return _eraBlock(group);
                                },
                              ),
                            ),
                          ),
                        ],
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

  Widget _eraBlock(_EraGroup group) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.primaryContainer,
            borderRadius: BorderRadius.circular(6),
          ),
          child: Text(
            _groupHeader(group),
            style: TextStyle(
              fontWeight: FontWeight.bold,
              color: Theme.of(context).colorScheme.onPrimaryContainer,
            ),
          ),
        ),
        const SizedBox(height: 6),
        for (int i = 0; i < group.events.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: _eventTile(group.events[i], i),
          ),
        const SizedBox(height: 12),
      ],
    );
  }

  Widget _eventTile(ExtractedFact event, int index) {
    final timeContext = event.extra['time_context']?.toString() ?? '';
    final era = (event.extra['era']?.toString() ?? '').trim();
    final storyOrder = (event.extra['story_time_order'] as num?)?.toInt() ?? 0;
    final chapterOrder = _chapterOrder(event);
    final chapterTitle = (event.extra['chapter_title']?.toString() ?? '').trim();
    final chapterLine = chapterOrder > 0
        ? (chapterTitle.isNotEmpty && chapterTitle != '第$chapterOrder章'
            ? '第$chapterOrder章 · $chapterTitle'
            : '第$chapterOrder章')
        : '章节不明';
    return ListTile(
      leading: CircleAvatar(child: Text('${index + 1}')),
      title: Text(event.content),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (era.isNotEmpty)
            Text('时代：$era${storyOrder > 0 ? '（故事时序 $storyOrder）' : ''}'),
          if (timeContext.isNotEmpty) Text('时间线索：$timeContext'),
          Text('章节：$chapterLine'),
          if (event.sourceQuote.isNotEmpty) Text('原文：${event.sourceQuote}'),
          Text('置信度：${event.confidence} · 状态：${event.status}'),
        ],
      ),
      isThreeLine: true,
    );
  }
}

class _EraGroup {
  _EraGroup({
    required this.era,
    required this.events,
  });

  final String era;
  final List<ExtractedFact> events;
}