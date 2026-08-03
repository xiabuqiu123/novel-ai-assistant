import 'dart:async';

import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

import 'shared_widgets.dart';

class RelationshipGraphScreen extends StatefulWidget {
  const RelationshipGraphScreen({super.key, required this.apiClient, required this.novel});

  final NovelApiClient apiClient;
  final Novel novel;

  @override
  State<RelationshipGraphScreen> createState() => _RelationshipGraphScreenState();
}

class _RelationshipGraphScreenState extends State<RelationshipGraphScreen> {
  Future<RelationshipGraphResult>? _graphFuture;
  bool _isRefreshing = false;
  bool _isClearingCache = false;
  int? _jobId;
  String? _jobStatus;
  int _pollToken = 0;

  @override
  void initState() {
    super.initState();
    _graphFuture = _load();
  }

  @override
  void dispose() {
    _pollToken++;
    super.dispose();
  }

  static RelationshipGraphResult _cancelledGraph(int novelId) {
    return RelationshipGraphResult(novelId: novelId, nodes: const [], edges: const []);
  }

  Future<RelationshipGraphResult> _load({bool forceRefresh = false}) async {
    final token = ++_pollToken;
    if (forceRefresh) {
      final startResult = await widget.apiClient.startRelationships(
        novelId: widget.novel.id,
        forceRefresh: forceRefresh,
      );
      if (!mounted || token != _pollToken) {
        return _cancelledGraph(widget.novel.id);
      }
      setState(() {
        _jobId = startResult.jobId;
        _jobStatus = startResult.status;
      });
      return _pollUntilDone(startResult.jobId, token);
    }
    // 只读进入：不自动创建任务。有 queued/running 任务则跟随轮询，否则直接展示已有图谱。
    final jobs = await widget.apiClient.listAnalysisJobs(novelId: widget.novel.id);
    if (!mounted || token != _pollToken) {
      return _cancelledGraph(widget.novel.id);
    }
    AnalysisJob? latest;
    for (final job in jobs) {
      if (job.taskType == 'relationship_extraction' && (latest == null || job.id > latest.id)) {
        latest = job;
      }
    }
    final activeJob = latest;
    if (activeJob == null || activeJob.status == 'cancelled') {
      return _cancelledGraph(widget.novel.id);
    }
    if (activeJob.status == 'queued' || activeJob.status == 'running') {
      setState(() {
        _jobId = activeJob.id;
        _jobStatus = activeJob.status;
      });
      return _pollUntilDone(activeJob.id, token);
    }
    if (activeJob.status == 'failed') {
      throw Exception(activeJob.error.isNotEmpty ? activeJob.error : '人物关系抽取任务失败');
    }
    return _fetchGraph(token);
  }

  Future<RelationshipGraphResult> _pollUntilDone(int jobId, int token) async {
    while (true) {
      await Future.delayed(const Duration(seconds: 2));
      if (!mounted || token != _pollToken) {
        return _cancelledGraph(widget.novel.id);
      }
      try {
        final job = await widget.apiClient.getAnalysisJob(jobId);
        if (mounted && token == _pollToken) {
          setState(() {
            _jobStatus = job.status;
          });
        }
        if (job.status == 'completed') {
          return _fetchGraph(token);
        }
        if (job.status == 'failed') {
          throw Exception(job.error.isNotEmpty ? job.error : '人物关系抽取任务失败');
        }
        if (job.status == 'cancelled') {
          return _cancelledGraph(widget.novel.id);
        }
      } catch (error) {
        if (error is Exception && error.toString().contains('任务失败')) {
          rethrow;
        }
        // Continue polling on transient errors.
      }
    }
  }

  Future<RelationshipGraphResult> _fetchGraph(int token) async {
    final graph = await widget.apiClient.fetchRelationshipGraph(novelId: widget.novel.id);
    if (!mounted || token != _pollToken) {
      return _cancelledGraph(widget.novel.id);
    }
    // E4: superseded edges/nodes belong to an older run; only show live facts.
    final liveEdges = graph.edges
        .where((edge) => edge.status != 'superseded')
        .toList();
    final liveNodes = graph.nodes
        .where((node) => node.status != 'superseded')
        .toList();
    return RelationshipGraphResult(novelId: graph.novelId, nodes: liveNodes, edges: liveEdges);
  }

  void _retry() {
    setState(() {
      _graphFuture = _load();
    });
  }

  Future<void> _clearRelationshipCache() async {
    setState(() {
      _isClearingCache = true;
    });
    try {
      final result = await widget.apiClient.clearNovelCache(widget.novel.id, taskType: 'relationship_extraction');
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已清除 ${result.deletedCacheEntries} 条关系缓存')),
      );
      _retry();
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('清除缓存失败: $error')),
      );
    } finally {
      if (mounted) {
        setState(() {
          _isClearingCache = false;
        });
      }
    }
  }

  Future<void> _refresh() async {
    setState(() {
      _isRefreshing = true;
    });
    try {
      final result = await _load(forceRefresh: true);
      if (!mounted) return;
      setState(() {
        _graphFuture = Future.value(result);
      });
    } catch (_) {
      if (!mounted) return;
      _retry();
    } finally {
      if (mounted) {
        setState(() {
          _isRefreshing = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('关系图: ${widget.novel.title}'),
        actions: [
          IconButton(
            key: const Key('clear-relationship-cache-button'),
            tooltip: '清除关系缓存',
            icon: _isClearingCache
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.cleaning_services_outlined),
            onPressed: _isClearingCache ? null : _clearRelationshipCache,
          ),
          IconButton(
            key: const Key('refresh-relationship-graph-button'),
            tooltip: '强制刷新',
            icon: _isRefreshing
                ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.refresh),
            onPressed: _isRefreshing ? null : _refresh,
          ),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: FutureBuilder<RelationshipGraphResult>(
              future: _graphFuture,
              builder: (context, snapshot) {
                if (snapshot.connectionState == ConnectionState.waiting) {
                  return Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const CircularProgressIndicator(),
                        const SizedBox(height: 12),
                        Text(
                          _jobStatus != null
                              ? '正在抽取人物关系… 任务 #${_jobId ?? 0}($_jobStatus)'
                              : '正在抽取人物关系…',
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 4),
                        Text(
                          '任务在后台运行，可切换页面，回来自动续看',
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ],
                    ),
                  );
                }
                if (snapshot.hasError) {
                  return Padding(
                    padding: const EdgeInsets.all(16),
                    child: ErrorState(message: snapshot.error.toString(), onRetry: _retry),
                  );
                }
                final graph = snapshot.data;
                if (graph == null || graph.edges.isEmpty) {
                  return const Center(
                    child: Padding(
                      padding: EdgeInsets.all(24),
                      child: Text(
                        '尚未抽取到人物关系。建议先运行人物抽取, 再点击右上角刷新。',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  );
                }
                final grouped = <String, List<RelationshipEdge>>{};
                for (final edge in graph.edges) {
                  grouped.putIfAbsent(edge.source, () => <RelationshipEdge>[]).add(edge);
                }
                final groupNames = grouped.keys.toList()..sort();
                return ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    Text(
                      '${graph.nodes.length} 个人物, ${graph.edges.length} 条关系',
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                    const SizedBox(height: 8),
                    Text(
                      '关系为 AI 待复核结论, 确认前请核对原文引文。',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    const SizedBox(height: 12),
                    ...groupNames.map(
                      (name) => _CharacterRelationshipGroup(name: name, edges: grouped[name]!),
                    ),
                    const SizedBox(height: 8),
                    Card(
                      clipBehavior: Clip.antiAlias,
                      child: ExpansionTile(
                        title: const Text('图谱视图（实验性）'),
                        subtitle: const Text('圆圈布局，仅供参考'),
                        initiallyExpanded: false,
                        children: [
                          SizedBox(
                            height: 320,
                            child: CustomPaint(
                              painter: RelationshipGraphPainter(graph: graph),
                              child: const SizedBox.expand(),
                            ),
                          ),
                        ],
                      ),
                    ),
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

class _CharacterRelationshipGroup extends StatelessWidget {
  const _CharacterRelationshipGroup({required this.name, required this.edges});

  final String name;
  final List<RelationshipEdge> edges;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.only(top: 8, bottom: 4),
          child: Row(
            children: [
              Icon(Icons.person, size: 18, color: Theme.of(context).colorScheme.primary),
              const SizedBox(width: 6),
              Text(name, style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(width: 8),
              Text('${edges.length} 条关系', style: Theme.of(context).textTheme.bodySmall),
            ],
          ),
        ),
        ...edges.map((edge) => _RelationshipEdgeCard(edge: edge)),
      ],
    );
  }
}

class _RelationshipEdgeCard extends StatelessWidget {
  const _RelationshipEdgeCard({required this.edge});

  final RelationshipEdge edge;

  static const Map<String, String> _attitudeLabels = {
    'hostile': '敌对',
    'cold': '冷淡',
    'neutral': '中立',
    'friendly': '友好',
    'close': '亲密',
  };

  static Color _attitudeColor(String attitude) {
    switch (attitude) {
      case 'hostile':
        return Colors.red;
      case 'cold':
        return Colors.blueGrey;
      case 'friendly':
        return Colors.teal;
      case 'close':
        return Colors.green;
      default:
        return Colors.grey;
    }
  }

  String get _displayLabel => edge.relationLabel.isNotEmpty ? edge.relationLabel : edge.relationType;

  String get _evolutionText => edge.evolution
      .map((item) {
        final chapter = item.chapterOrder != null ? '第 ${item.chapterOrder} 章：' : '';
        final event = item.event.isNotEmpty ? '（${item.event}）' : '';
        return '$chapter${item.relationLabel}$event';
      })
      .join(' → ');

  @override
  Widget build(BuildContext context) {
    final badgeColor = edge.confidence == 'high'
        ? Colors.green
        : edge.confidence == 'medium'
            ? Colors.orange
            : Colors.grey;
    final chapterLabel = edge.chapterOrder != null
        ? '第 ${edge.chapterOrder} 章${edge.chapterTitle.isNotEmpty ? ': ${edge.chapterTitle}' : ''}'
        : (edge.chapterTitle.isNotEmpty ? edge.chapterTitle : '未知章节');
    final attitudeLabel = _attitudeLabels[edge.attitude];
    final attitudeColor = _attitudeColor(edge.attitude);
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ExpansionTile(
        title: Row(
          children: [
            Expanded(
              child: Text(
                '${edge.source} -[$_displayLabel]-> ${edge.target}',
                style: Theme.of(context).textTheme.titleSmall,
              ),
            ),
            if (attitudeLabel != null) ...[
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: attitudeColor.withAlpha(30),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  attitudeLabel,
                  style: TextStyle(fontSize: 12, color: attitudeColor),
                ),
              ),
              const SizedBox(width: 8),
            ],
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                color: badgeColor.withAlpha(30),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Text(
                displayConfidence(edge.confidence),
                style: TextStyle(fontSize: 12, color: badgeColor),
              ),
            ),
            const SizedBox(width: 8),
            Chip(
              label: Text(displayFactStatus(edge.status), style: const TextStyle(fontSize: 11)),
              padding: EdgeInsets.zero,
              materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
          ],
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (edge.description.isNotEmpty) Text(edge.description),
            Text(chapterLabel, style: Theme.of(context).textTheme.bodySmall),
            if (edge.evolution.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  '关系变化: $_evolutionText',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
          ],
        ),
        children: [
          if (edge.sourceQuote.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              child: Text(
                edge.sourceQuote,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
              ),
            ),
        ],
      ),
    );
  }
}

class RelationshipGraphPainter extends CustomPainter {
  RelationshipGraphPainter({required this.graph});

  final RelationshipGraphResult graph;

  @override
  void paint(Canvas canvas, Size size) {
    final nodes = graph.nodes;
    if (nodes.isEmpty) return;
    final center = Offset(size.width / 2, size.height / 2);
    final radius = math.max(40.0, math.min(size.width, size.height) / 2 - 48);
    final positions = <String, Offset>{};
    for (int i = 0; i < nodes.length; i++) {
      final angle = -math.pi / 2 + 2 * math.pi * i / nodes.length;
      positions[nodes[i].name] = center + Offset(radius * math.cos(angle), radius * math.sin(angle));
    }
    final edgePaint = Paint()
      ..color = Colors.blueGrey.withAlpha(120)
      ..strokeWidth = 1.5;
    final textPainter = TextPainter(textDirection: TextDirection.ltr);
    for (final edge in graph.edges) {
      final from = positions[edge.source];
      final to = positions[edge.target];
      if (from == null || to == null) continue;
      canvas.drawLine(from, to, edgePaint);
      final mid = Offset((from.dx + to.dx) / 2, (from.dy + to.dy) / 2);
      textPainter.text = TextSpan(
        text: edge.relationLabel.isNotEmpty ? edge.relationLabel : edge.relationType,
        style: const TextStyle(fontSize: 10, color: Colors.blueGrey),
      );
      textPainter.layout(maxWidth: 120);
      textPainter.paint(canvas, mid - Offset(textPainter.width / 2, textPainter.height / 2));
    }
    final nodePaint = Paint()..color = Colors.teal;
    final borderPaint = Paint()
      ..color = Colors.white
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;
    for (final node in nodes) {
      final pos = positions[node.name]!;
      canvas.drawCircle(pos, 16, nodePaint);
      canvas.drawCircle(pos, 16, borderPaint);
      textPainter.text = TextSpan(
        text: node.name,
        style: const TextStyle(fontSize: 11, color: Colors.black87),
      );
      textPainter.layout(maxWidth: 110);
      textPainter.paint(canvas, pos + Offset(-textPainter.width / 2, 20));
    }
  }

  @override
  bool shouldRepaint(covariant RelationshipGraphPainter oldDelegate) => oldDelegate.graph != graph;
}
