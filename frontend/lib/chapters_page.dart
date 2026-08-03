import 'dart:async';

import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

import 'analysis_jobs_page.dart';
import 'chapter_reader_page.dart';
import 'characters_page.dart';
import 'export_report_page.dart';
import 'conflict_page.dart';
import 'qa_page.dart';
import 'relationship_graph_page.dart';
import 'settings_page.dart';
import 'shared_widgets.dart';
import 'timeline_page.dart';

class ChapterListScreen extends StatefulWidget {
  const ChapterListScreen({super.key, required this.apiClient, required this.novel});

  final NovelApiClient apiClient;
  final Novel novel;

  @override
  State<ChapterListScreen> createState() => _ChapterListScreenState();
}

class _ChapterListScreenState extends State<ChapterListScreen> {
  Future<List<ChapterSummary>>? _chaptersFuture;
  bool _isGeneratingOutline = false;
  bool _isClearingOutlineCache = false;
  BookOutlineResult? _outlineResult;
  String? _outlineError;
  bool _isAnalyzingAll = false;
  int? _analyzeAllJobId;
  String? _analyzeAllJobStatus;
  int _analyzeAllProgress = 0;
  String? _analyzeAllError;
  Timer? _analyzeAllPollTimer;

  @override
  void initState() {
    super.initState();
    _chaptersFuture = _load();
  }

  Future<List<ChapterSummary>> _load() {
    return widget.apiClient.listChapters(widget.novel.id);
  }

  void _retry() {
    setState(() {
      _chaptersFuture = _load();
    });
  }

  Future<void> _generateOutline({bool forceRefresh = false}) async {
    setState(() {
      _isGeneratingOutline = true;
      _outlineError = null;
      _outlineResult = null;
      _outlinePollTimer?.cancel();
      _outlinePollTimer = null;
    });
    try {
      final startResult = await widget.apiClient.startOutline(widget.novel.id, forceRefresh: forceRefresh);
      if (!mounted) return;
      final jobId = startResult.jobId;
      setState(() {
        _outlineJobId = jobId;
        _outlineJobStatus = startResult.status;
      });
      _pollOutlineJob(jobId);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _outlineError = error.toString();
        _isGeneratingOutline = false;
      });
    }
  }

  Timer? _outlinePollTimer;
  int? _outlineJobId;
  String? _outlineJobStatus;
  int _outlinePollCount = 0;
  Timer? _stageOutlinePollTimer;
  int? _stageOutlineJobId;
  String? _stageOutlineJobStatus;
  int _stageOutlinePollCount = 0;
  bool _isGeneratingStageOutline = false;
  bool _isClearingStageOutlineCache = false;
  BookStageOutlineResult? _stageOutlineResult;
  String? _stageOutlineError;

  void _pollOutlineJob(int jobId) {
    _outlinePollTimer?.cancel();
    _outlinePollCount = 0;
    _runPollLoop(jobId);
  }

  Future<void> _runPollLoop(int jobId) async {
    while (true) {
      _outlinePollCount += 1;
      try {
        final job = await widget.apiClient.getAnalysisJob(jobId);
        if (!mounted) return;
        setState(() {
          _outlineJobStatus = job.status;
        });
        if (job.status == 'completed') {
          final jobResult = await widget.apiClient.getJobResult(jobId);
          if (!mounted) return;
          final merged = jobResult.mergedResult();
          if (merged != null) {
            setState(() {
              _outlineResult = BookOutlineResult.fromJson(merged);
              _isGeneratingOutline = false;
            });
          }
          return;
        } else if (job.status == 'failed') {
          if (!mounted) return;
          final jobResult = await widget.apiClient.getJobResult(jobId);
          final merged = jobResult.mergedResult();
          if (merged != null) {
            setState(() {
              _outlineResult = BookOutlineResult.fromJson(merged);
              _isGeneratingOutline = false;
            });
            return;
          }
          setState(() {
            _outlineError = job.error.isNotEmpty ? job.error : '任务失败';
            _isGeneratingOutline = false;
          });
          return;
        }
        await Future<void>.delayed(const Duration(seconds: 2));
      } catch (error) {
        if (!mounted) return;
        setState(() {
          _outlineError = error.toString();
          _isGeneratingOutline = false;
        });
        return;
      }
    }
  }



  Future<void> _generateStageOutline({bool forceRefresh = false}) async {
    setState(() {
      _isGeneratingStageOutline = true;
      _stageOutlineError = null;
      _stageOutlineResult = null;
      _stageOutlinePollTimer?.cancel();
      _stageOutlinePollTimer = null;
    });
    try {
      final startResult = await widget.apiClient.startStageOutline(widget.novel.id, forceRefresh: forceRefresh);
      if (!mounted) return;
      final jobId = startResult.jobId;
      setState(() {
        _stageOutlineJobId = jobId;
        _stageOutlineJobStatus = startResult.status;
      });
      _pollStageOutlineJob(jobId);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _stageOutlineError = error.toString();
        _isGeneratingStageOutline = false;
      });
    }
  }

  void _pollStageOutlineJob(int jobId) {
    _stageOutlinePollTimer?.cancel();
    _stageOutlinePollCount = 0;
    _runStageOutlinePollLoop(jobId);
  }

  Future<void> _runStageOutlinePollLoop(int jobId) async {
    while (true) {
      _stageOutlinePollCount += 1;
      try {
        final job = await widget.apiClient.getAnalysisJob(jobId);
        if (!mounted) return;
        setState(() {
          _stageOutlineJobStatus = job.status;
        });
        if (job.status == 'completed') {
          final jobResult = await widget.apiClient.getJobResult(jobId);
          if (!mounted) return;
          final merged = jobResult.mergedResult();
          if (merged != null) {
            setState(() {
              _stageOutlineResult = BookStageOutlineResult.fromJson(merged);
              _isGeneratingStageOutline = false;
            });
          }
          return;
        } else if (job.status == 'failed') {
          if (!mounted) return;
          final jobResult = await widget.apiClient.getJobResult(jobId);
          final merged = jobResult.mergedResult();
          if (merged != null) {
            setState(() {
              _stageOutlineResult = BookStageOutlineResult.fromJson(merged);
              _isGeneratingStageOutline = false;
            });
            return;
          }
          setState(() {
            _stageOutlineError = job.error.isNotEmpty ? job.error : '任务失败';
            _isGeneratingStageOutline = false;
          });
          return;
        }
        await Future<void>.delayed(const Duration(seconds: 2));
      } catch (error) {
        if (!mounted) return;
        setState(() {
          _stageOutlineError = error.toString();
          _isGeneratingStageOutline = false;
        });
        return;
      }
    }
  }

  Future<void> _clearStageOutlineCache() async {
    setState(() {
      _isClearingStageOutlineCache = true;
      _stageOutlineError = null;
    });
    try {
      final result = await widget.apiClient.clearNovelCache(widget.novel.id, taskType: 'book_stage_outline');
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已清除 ${result.deletedCacheEntries} 条大纲缓存')),
      );
      setState(() {
        _stageOutlineResult = null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _stageOutlineError = error.toString();
      });
    } finally {
      if (mounted) {
        setState(() {
          _isClearingStageOutlineCache = false;
        });
      }
    }
  }

  Future<void> _analyzeWholeBook() async {
    setState(() {
      _isAnalyzingAll = true;
      _analyzeAllError = null;
      _analyzeAllProgress = 0;
      _analyzeAllPollTimer?.cancel();
      _analyzeAllPollTimer = null;
    });
    try {
      final startResult = await widget.apiClient.startWholeBookAnalysis(widget.novel.id);
      if (!mounted) return;
      setState(() {
        _analyzeAllJobId = startResult.jobId;
        _analyzeAllJobStatus = startResult.status;
      });
      _pollAnalyzeAllJob(startResult.jobId);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _analyzeAllError = error.toString();
        _isAnalyzingAll = false;
      });
    }
  }

  void _pollAnalyzeAllJob(int jobId) {
    _analyzeAllPollTimer?.cancel();
    _runAnalyzeAllPollLoop(jobId);
  }

  Future<void> _runAnalyzeAllPollLoop(int jobId) async {
    while (true) {
      if (!mounted || _analyzeAllJobId != jobId) return;
      try {
        final job = await widget.apiClient.getAnalysisJob(jobId);
        if (!mounted || _analyzeAllJobId != jobId) return;
        setState(() {
          _analyzeAllJobStatus = job.status;
          _analyzeAllProgress = job.progress;
        });
        if (job.status == 'completed') {
          setState(() {
            _isAnalyzingAll = false;
            _analyzeAllJobId = null;
          });
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('全书分析已完成')),
          );
          return;
        }
        if (job.status == 'failed' || job.status == 'cancelled') {
          setState(() {
            _isAnalyzingAll = false;
            _analyzeAllJobId = null;
            if (job.status == 'failed') {
              _analyzeAllError = job.error.isNotEmpty ? job.error : '任务失败';
            }
          });
          return;
        }
        await Future<void>.delayed(const Duration(seconds: 2));
      } catch (error) {
        if (!mounted) return;
        setState(() {
          _analyzeAllError = error.toString();
          _isAnalyzingAll = false;
        });
        return;
      }
    }
  }

  Future<void> _cancelWholeBookAnalysis() async {
    final jobId = _analyzeAllJobId;
    if (jobId == null) return;
    try {
      await widget.apiClient.cancelAnalysisJob(jobId);
      if (!mounted) return;
      setState(() {
        _analyzeAllJobStatus = 'cancelled';
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _analyzeAllError = error.toString();
      });
    }
  }

  @override
  void dispose() {
    _outlinePollTimer?.cancel();
    _stageOutlinePollTimer?.cancel();
    _analyzeAllPollTimer?.cancel();
    super.dispose();
  }

  Future<void> _clearOutlineCache() async {
    setState(() {
      _isClearingOutlineCache = true;
      _outlineError = null;
    });
    try {
      final result = await widget.apiClient.clearNovelCache(widget.novel.id, taskType: 'book_outline');
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已清除 ${result.deletedCacheEntries} 条章纲缓存')),
      );
      setState(() {
        _outlineResult = null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _outlineError = error.toString();
      });
    } finally {
      if (mounted) {
        setState(() {
          _isClearingOutlineCache = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.novel.title),
        actions: [
          IconButton(
            icon: const Icon(Icons.chat_outlined),
            tooltip: 'AI 问答',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => QaScreen(apiClient: widget.apiClient, novel: widget.novel),
              ),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.people_outlined),
            tooltip: '人物',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => CharacterProfilesScreen(apiClient: widget.apiClient, novel: widget.novel),
              ),
            ),
          ),
          IconButton(
            key: const Key('open-relationship-graph-button'),
            icon: const Icon(Icons.hub_outlined),
            tooltip: '关系图',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => RelationshipGraphScreen(apiClient: widget.apiClient, novel: widget.novel),
              ),
            ),
          ),
          IconButton(
            key: const Key('open-settings-button'),
            icon: const Icon(Icons.public_outlined),
            tooltip: '设定',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => SettingsScreen(apiClient: widget.apiClient, novel: widget.novel),
              ),
            ),
          ),
          IconButton(
            key: const Key('open-timeline-button'),
            icon: const Icon(Icons.timeline_outlined),
            tooltip: '时间线',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => TimelineScreen(apiClient: widget.apiClient, novel: widget.novel),
              ),
            ),
          ),
          IconButton(
            key: const Key('open-conflicts-button'),
            icon: const Icon(Icons.report_problem_outlined),
            tooltip: '设定冲突',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => ConflictDetectionScreen(apiClient: widget.apiClient, novel: widget.novel),
              ),
            ),
          ),
          IconButton(
            key: const Key('open-export-report-button'),
            icon: const Icon(Icons.summarize_outlined),
            tooltip: '导出报告',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => ExportReportScreen(apiClient: widget.apiClient, novel: widget.novel),
              ),
            ),
          ),
          IconButton(
            key: const Key('open-analysis-jobs-button'),
            icon: const Icon(Icons.assignment_outlined),
            tooltip: '分析任务',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => AnalysisJobsScreen(apiClient: widget.apiClient, novelId: widget.novel.id),
              ),
            ),
          ),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: FutureBuilder<List<ChapterSummary>>(
              future: _chaptersFuture,
              builder: (context, snapshot) {
                final isLoading = snapshot.connectionState == ConnectionState.waiting;
                final error = snapshot.error;
                final chapters = snapshot.data ?? <ChapterSummary>[];

                return RefreshIndicator(
                  onRefresh: () async => _retry(),
                  child: ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.all(16),
                    children: [
                      Text('章节列表', style: Theme.of(context).textTheme.titleLarge),
                      const SizedBox(height: 8),
                      Text(
                        '共 ${widget.novel.chapterCount} 章, ${widget.novel.chunkCount} 个分块',
                        style: Theme.of(context).textTheme.bodyMedium,
                      ),
                      Text('全书大纲', style: Theme.of(context).textTheme.titleMedium),
                      const SizedBox(height: 4),
                      Text(
                        '按剧情阶段分块：地点 / 人物 / 事件 / 解决 / 结果',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                      const SizedBox(height: 8),
                      FilledButton.icon(
                        key: const Key('generate-stage-outline-button'),
                        onPressed: _isGeneratingStageOutline ? null : () => _generateStageOutline(),
                        icon: _isGeneratingStageOutline
                            ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.view_module_outlined),
                        label: Text(_isGeneratingStageOutline
                            ? (_stageOutlineJobId != null ? '任务 #$_stageOutlineJobId - $_stageOutlineJobStatus' : '生成中')
                            : '生成大纲'),
                      ),
                      const SizedBox(height: 8),
                      Row(
                        children: [
                          Expanded(
                            child: OutlinedButton.icon(
                              key: const Key('force-refresh-stage-outline-button'),
                              onPressed: _isGeneratingStageOutline ? null : () => _generateStageOutline(forceRefresh: true),
                              icon: const Icon(Icons.refresh),
                              label: const Text('强制刷新大纲'),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: OutlinedButton.icon(
                              key: const Key('clear-stage-outline-cache-button'),
                              onPressed: _isClearingStageOutlineCache ? null : _clearStageOutlineCache,
                              icon: _isClearingStageOutlineCache
                                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                                  : const Icon(Icons.cleaning_services_outlined),
                              label: Text(_isClearingStageOutlineCache ? '清理中' : '清除大纲缓存'),
                            ),
                          ),
                        ],
                      ),
                      if (_stageOutlineJobId != null && _stageOutlineJobStatus != null && _stageOutlineJobStatus != 'completed' && _stageOutlineJobStatus != 'failed') ...[
                        const SizedBox(height: 12),
                        Card(
                          child: Padding(
                            padding: const EdgeInsets.all(12),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  children: [
                                    const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
                                    const SizedBox(width: 12),
                                    Expanded(child: Text('任务 #$_stageOutlineJobId: $_stageOutlineJobStatus(轮询 $_stageOutlinePollCount)')),
                                  ],
                                ),
                                const SizedBox(height: 6),
                                Text('任务在后台运行，可切换页面，回来自动续看', style: Theme.of(context).textTheme.bodySmall),
                              ],
                            ),
                          ),
                        ),
                      ],
                      if (_stageOutlineError != null) ...[
                        const SizedBox(height: 16),
                        ErrorState(message: _stageOutlineError!, onRetry: _generateStageOutline),
                      ],
                      if (_stageOutlineResult != null) ...[
                        const SizedBox(height: 16),
                        BookStageOutlinePanel(result: _stageOutlineResult!),
                      ],
                      const SizedBox(height: 24),
                      Text('章纲', style: Theme.of(context).textTheme.titleMedium),
                      const SizedBox(height: 8),
                      FilledButton.icon(
                        key: const Key('generate-outline-button'),
                        onPressed: _isGeneratingOutline ? null : () => _generateOutline(),
                        icon: _isGeneratingOutline
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.account_tree_outlined),
                        label: Text(_isGeneratingOutline
                            ? (_outlineJobId != null ? '任务 #$_outlineJobId - $_outlineJobStatus' : '生成中')
                            : '生成章纲'),
                      ),
                      const SizedBox(height: 8),
                      Row(
                        children: [
                          Expanded(
                            child: OutlinedButton.icon(
                              key: const Key('force-refresh-outline-button'),
                              onPressed: _isGeneratingOutline ? null : () => _generateOutline(forceRefresh: true),
                              icon: const Icon(Icons.refresh),
                              label: const Text('强制刷新章纲'),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: OutlinedButton.icon(
                              key: const Key('clear-outline-cache-button'),
                              onPressed: _isClearingOutlineCache ? null : _clearOutlineCache,
                              icon: _isClearingOutlineCache
                                  ? const SizedBox(
                                      width: 18,
                                      height: 18,
                                      child: CircularProgressIndicator(strokeWidth: 2),
                                    )
                                  : const Icon(Icons.cleaning_services_outlined),
                              label: Text(_isClearingOutlineCache ? '清理中' : '清除章纲缓存'),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      FilledButton.icon(
                        key: const Key('analyze-whole-book-button'),
                        onPressed: _isAnalyzingAll ? null : _analyzeWholeBook,
                        icon: _isAnalyzingAll
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.auto_awesome_outlined),
                        label: Text(_isAnalyzingAll
                            ? '全书分析中 - ${_analyzeAllJobStatus ?? 'queued'} $_analyzeAllProgress%'
                            : '一键分析全书'),
                      ),
                      if (_isAnalyzingAll) ...[
                        const SizedBox(height: 8),
                        LinearProgressIndicator(value: _analyzeAllProgress / 100),
                        const SizedBox(height: 4),
                        Text(
                          '任务在后台运行，可切换页面，回来自动续看',
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                        const SizedBox(height: 4),
                        OutlinedButton.icon(
                          key: const Key('cancel-whole-book-button'),
                          onPressed: _cancelWholeBookAnalysis,
                          icon: const Icon(Icons.stop_outlined),
                          label: const Text('取消分析'),
                        ),
                      ],
                      if (_analyzeAllError != null) ...[
                        const SizedBox(height: 8),
                        Text(
                          _analyzeAllError!,
                          style: TextStyle(color: Theme.of(context).colorScheme.error),
                        ),
                      ],
                      if (_outlineJobId != null && _outlineJobStatus != null && _outlineJobStatus != 'completed' && _outlineJobStatus != 'failed') ...[
                        const SizedBox(height: 12),
                        Card(
                          child: Padding(
                            padding: EdgeInsets.all(12),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  children: [
                                    SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
                                    SizedBox(width: 12),
                                    Expanded(child: Text('任务 #$_outlineJobId: $_outlineJobStatus(轮询 $_outlinePollCount)')),
                                  ],
                                ),
                                const SizedBox(height: 6),
                                Text(
                                  '任务在后台运行，可切换页面，回来自动续看',
                                  style: Theme.of(context).textTheme.bodySmall,
                                ),
                              ],
                            ),
                          ),
                        ),
                      ],
                      if (_outlineError != null) ...[
                        const SizedBox(height: 16),
                        ErrorState(message: _outlineError!, onRetry: _generateOutline),
                      ],
                      if (_outlineResult != null) ...[
                        const SizedBox(height: 16),
                        ExpansionTile(
                          key: const Key('chapter-outline-expansion'),
                          initiallyExpanded: true,
                          title: const Text('章纲（逐章，可折叠）'),
                          children: [
                            BookOutlinePanel(result: _outlineResult!),
                          ],
                        ),
                      ],
                      const SizedBox(height: 16),
                      if (isLoading)
                        const LinearProgressIndicator()
                      else if (error != null)
                        ErrorState(message: error.toString(), onRetry: _retry)
                      else if (chapters.isEmpty)
                        const EmptyChapterState()
                      else
                        ...chapters.map(
                          (chapter) => ChapterListTile(
                            chapter: chapter,
                            onTap: () {
                              Navigator.of(context).push(
                                MaterialPageRoute<void>(
                                  builder: (context) => ChapterReaderScreen(
                                    apiClient: widget.apiClient,
                                    chapterSummary: chapter,
                                  ),
                                ),
                              );
                            },
                          ),
                        ),
                    ],
                  ),
                );
              },
            ),
          ),
        ),
      ),
    );
  }
}

class BookOutlinePanel extends StatelessWidget {
  const BookOutlinePanel({super.key, required this.result});

  final BookOutlineResult result;

  @override
  Widget build(BuildContext context) {
    return Card(
      key: const Key('book-outline-result'),
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('章纲（逐章）', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Text(result.title),
            const SizedBox(height: 12),
            if (result.hasParseError) ...[
              Text(
                '章纲解析错误: ${result.parseError}',
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
              const SizedBox(height: 12),
            ],
            if (result.chapters.isEmpty)
              const Text('未返回章纲章节')
            else
              ...result.chapters.map(
                (chapter) => Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text(
                    '第 ${chapter.order} 章: ${chapter.title}\n${chapter.brief}',
                  ),
                ),
              ),
            const SizedBox(height: 8),
            ProvenancePanel(status: result.status, provenance: result.provenance),
          ],
        ),
      ),
    );
  }
}

class BookStageOutlinePanel extends StatelessWidget {
  const BookStageOutlinePanel({super.key, required this.result});

  final BookStageOutlineResult result;

  @override
  Widget build(BuildContext context) {
    return Card(
      key: const Key('book-stage-outline-result'),
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('全书大纲', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            if (result.hasParseError) ...[
              Text(
                '大纲解析错误: ${result.parseError}',
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
              const SizedBox(height: 12),
            ],
            if (result.stages.isEmpty)
              const Text('未返回阶段')
            else
              ...result.stages.map((stage) => _StageCard(stage: stage)),
            if (result.evidence.isNotEmpty) ...[
              const SizedBox(height: 8),
              ExpansionTile(
                key: const Key('stage-outline-evidence'),
                initiallyExpanded: false,
                title: Text('证据（${result.evidence.length} 条）'),
                children: result.evidence
                    .map((ev) => _StageEvidenceItem(evidence: ev))
                    .toList(),
              ),
            ],
            const SizedBox(height: 8),
            ProvenancePanel(status: result.status, provenance: result.provenance),
          ],
        ),
      ),
    );
  }
}

class _StageCard extends StatelessWidget {
  const _StageCard({required this.stage});

  final BookStageOutlineStage stage;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      key: Key('stage-card-${stage.stageIndex}'),
      margin: const EdgeInsets.only(bottom: 10),
      color: theme.colorScheme.surfaceContainerHighest,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              stage.title.isNotEmpty ? stage.title : '阶段 ${stage.stageIndex}',
              style: theme.textTheme.titleSmall,
            ),
            const SizedBox(height: 4),
            Text(stage.chapterRange, style: theme.textTheme.bodySmall),
            const SizedBox(height: 8),
            _StageField(label: '地点', value: stage.location),
            _StageField(label: '人物', value: stage.characters.join('、')),
            _StageField(label: '事件', value: stage.event),
            _StageField(label: '解决', value: stage.resolution),
            _StageField(label: '结果', value: stage.outcome),
          ],
        ),
      ),
    );
  }
}

class _StageField extends StatelessWidget {
  const _StageField({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final display = value.trim().isEmpty ? '未提及' : value;
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: RichText(
        text: TextSpan(
          style: theme.textTheme.bodyMedium,
          children: [
            TextSpan(text: '$label：', style: const TextStyle(fontWeight: FontWeight.bold)),
            TextSpan(text: display),
          ],
        ),
      ),
    );
  }
}

class _StageEvidenceItem extends StatelessWidget {
  const _StageEvidenceItem({required this.evidence});

  final Map<String, dynamic> evidence;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final order = evidence['chapter_order'];
    final orderText = order is num ? '第 ${order.toInt()} 章' : '';
    final quote = (evidence['source_quote'] as String?) ?? '';
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (orderText.isNotEmpty)
            Text(orderText, style: theme.textTheme.bodySmall?.copyWith(fontWeight: FontWeight.bold)),
          if (orderText.isNotEmpty) const SizedBox(height: 2),
          Text(
            quote.trim().isEmpty ? '（无原文）' : quote,
            style: theme.textTheme.bodySmall,
          ),
          const Divider(height: 8),
        ],
      ),
    );
  }
}

class ChapterListTile extends StatelessWidget {
  const ChapterListTile({super.key, required this.chapter, required this.onTap});

  final ChapterSummary chapter;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      key: Key('chapter-list-tile-${chapter.id}'),
      margin: const EdgeInsets.only(bottom: 8),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      child: ListTile(
        leading: CircleAvatar(child: Text('${chapter.order}')),
        title: Text(chapter.title),
        subtitle: Text('${chapter.charCount} 字'),
        trailing: const Icon(Icons.chevron_right),
        onTap: onTap,
      ),
    );
  }
}

class EmptyChapterState extends StatelessWidget {
  const EmptyChapterState({super.key});

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 32),
      child: Center(child: Text('未找到章节')),
    );
  }
}
