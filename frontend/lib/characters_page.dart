import 'dart:async';

import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

import 'settings_page.dart';
import 'shared_widgets.dart';

class CharacterProfilesScreen extends StatefulWidget {
  const CharacterProfilesScreen({
    super.key,
    required this.apiClient,
    required this.novel,
    this.highlightCharacter,
  });

  final NovelApiClient apiClient;
  final Novel novel;
  final String? highlightCharacter;

  @override
  State<CharacterProfilesScreen> createState() => _CharacterProfilesScreenState();
}

class _CharacterProfilesScreenState extends State<CharacterProfilesScreen> {
  Future<CharacterListResult>? _resultFuture;
  bool _isRefreshing = false;
  bool _isClearingCache = false;

  @override
  void initState() {
    super.initState();
    _resultFuture = _load();
  }

  Timer? _charPollTimer;
  int? _charJobId;
  String? _charJobStatus;
  int _charPollCount = 0;

  Future<CharacterListResult> _load({bool forceRefresh = false}) {
    return _loadAsync(forceRefresh: forceRefresh);
  }

  Future<CharacterListResult> _loadAsync({bool forceRefresh = false}) async {
    if (forceRefresh) {
      final startResult = await _startJob(forceRefresh: true);
      return _loadWithJob(startResult.jobId);
    }
    // 只读进入：不自动创建任务。有 queued/running 任务则跟随轮询，否则直接展示已有结果。
    final jobs = await widget.apiClient.listAnalysisJobs(novelId: widget.novel.id);
    if (!mounted) return _emptyResult();
    AnalysisJob? latest;
    for (final job in jobs) {
      if (job.taskType == 'character_extraction' && (latest == null || job.id > latest.id)) {
        latest = job;
      }
    }
    if (latest == null || latest.status == 'cancelled') {
      return _emptyResult();
    }
    if (latest.status == 'queued' || latest.status == 'running') {
      return _loadWithJob(latest.id);
    }
    // completed / failed：直接读取已落库结果，不再发起模型任务。
    final jobResult = await widget.apiClient.getJobResult(latest.id);
    if (!mounted) return _emptyResult();
    final merged = jobResult.mergedResult();
    if (merged != null) {
      return CharacterListResult.fromJson(merged);
    }
    if (latest.status == 'failed') {
      throw Exception(latest.error.isNotEmpty ? latest.error : '任务失败');
    }
    return _emptyResult();
  }

  Future<JobStartResult> _startJob({bool forceRefresh = false}) async {
    final startResult = await widget.apiClient.startCharacters(
      novelId: widget.novel.id,
      forceRefresh: forceRefresh,
    );
    if (mounted) {
      setState(() {
        _charJobId = startResult.jobId;
        _charJobStatus = startResult.status;
      });
    }
    return startResult;
  }

  Future<CharacterListResult> _loadWithJob(int jobId) async {
    if (mounted) {
      setState(() {
        _charJobId = jobId;
      });
    }
    // Poll until complete (no fixed upper bound; backend job controls lifecycle)
    while (true) {
      await Future.delayed(const Duration(seconds: 2));
      if (!mounted) return _emptyResult();
      try {
        final job = await widget.apiClient.getAnalysisJob(jobId);
        setState(() {
          _charJobStatus = job.status;
          _charPollCount += 1;
        });
        if (job.status == 'completed' || job.status == 'failed') {
          final jobResult = await widget.apiClient.getJobResult(jobId);
          if (!mounted) return _emptyResult();
          final merged = jobResult.mergedResult();
          if (merged != null) {
            return CharacterListResult.fromJson(merged);
          }
          if (job.status == 'failed') {
            throw Exception(job.error.isNotEmpty ? job.error : '任务失败');
          }
          return _emptyResult();
        }
        if (job.status == 'cancelled') {
          return _emptyResult();
        }
      } catch (error) {
        if (_charJobStatus == 'failed' || (error is Exception && error.toString().contains('任务失败'))) {
          rethrow;
        }
        // Continue polling on transient errors
      }
    }
  }

  CharacterListResult _emptyResult() {
    return CharacterListResult(
      status: 'no_job',
      characters: const [],
      cacheHit: false,
      persistedFacts: 0,
      provenance: _emptyProvenance(),
    );
  }

  static ModelProvenance _emptyProvenance() {
    return ModelProvenance(
      taskType: '', modelUsed: '', source: '', cacheHit: false,
      localFallback: false, modelError: '', cacheKey: '', jobId: null,
      providerCallAttempted: false, providerCallSucceeded: false,
    );
  }

  void _retry() {
    setState(() {
      _charPollTimer?.cancel();
      _resultFuture = _load();
    });
  }

  Future<void> _refresh() async {
    setState(() {
      _isRefreshing = true;
      _charPollTimer?.cancel();
    });
    try {
      final result = await _load(forceRefresh: true);
      if (!mounted) return;
      setState(() {
        _resultFuture = Future.value(result);
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
  void dispose() {
    _charPollTimer?.cancel();
    super.dispose();
  }

  Future<void> _clearCharacterCache() async {
    setState(() {
      _isClearingCache = true;
    });
    try {
      final result = await widget.apiClient.clearNovelCache(widget.novel.id, taskType: 'character_extraction');
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已清除 ${result.deletedCacheEntries} 条人物缓存')),
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('人物: ${widget.novel.title}'),
        actions: [
          IconButton(
            key: const Key('clear-character-cache-button'),
            tooltip: '清除人物缓存',
            icon: _isClearingCache
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.cleaning_services_outlined),
            onPressed: _isClearingCache ? null : _clearCharacterCache,
          ),
          IconButton(
            icon: _isRefreshing
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.refresh),
            onPressed: _isRefreshing ? null : _refresh,
          ),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: FutureBuilder<CharacterListResult>(
              future: _resultFuture,
              builder: (context, snapshot) {
                final isLoading = snapshot.connectionState == ConnectionState.waiting;
                final error = snapshot.error;
                final result = snapshot.data;

                if (isLoading) {
                  return Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const CircularProgressIndicator(),
                      if (_charJobId != null) ...[
                        const SizedBox(height: 16),
                        Text('任务 #$_charJobId: $_charJobStatus',
                          style: Theme.of(context).textTheme.bodyMedium),
                        if (_charPollCount > 0)
                          Text('轮询 $_charPollCount',
                            style: Theme.of(context).textTheme.bodySmall),
                        const SizedBox(height: 4),
                        Text(
                          '任务在后台运行，可切换页面，回来自动续看',
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ],
                    ],
                  );
                }
                if (error != null) {
                  return ErrorState(message: error.toString(), onRetry: _retry);
                }
                if (result == null) {
                  return const EmptyBookshelfState();
                }

                // E4: superseded facts belong to an older run; only show live facts.
                final characters = result.characters
                    .where((char) => char.reviewStatus != 'superseded')
                    .toList();
                if (characters.isEmpty) {
                  return const Center(
                    child: Text('尚未抽取到人物, 点击右上角刷新重试。'),
                  );
                }

                final highlightName = widget.highlightCharacter?.trim();
                return ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    Text(
                      '共找到 ${characters.length} 个人物',
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                    const SizedBox(height: 8),
                    ProvenancePanel(status: result.status, provenance: result.provenance),
                    if (result.duplicateCandidates.isNotEmpty) ...[
                      const SizedBox(height: 8),
                      _DuplicateCandidatesBanner(candidates: result.duplicateCandidates),
                    ],
                    if (result.provenance.localFallback || result.provenance.source.contains('fallback')) ...[
                      const SizedBox(height: 8),
                      const Text(
                        '使用了本地兜底结果, 人物名为启发式匹配, 可能混入叙述片段; 请配置并测试模型后强制刷新以获得模型抽取结果。',
                      ),
                    ],
                    const SizedBox(height: 16),
                    ...characters.map(
                      (char) => _CharacterCard(
                        character: char,
                        apiClient: widget.apiClient,
                        novel: widget.novel,
                        highlighted: highlightName != null &&
                            (char.name == highlightName || char.aliases.contains(highlightName)),
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

class _CharacterCard extends StatelessWidget {
  const _CharacterCard({
    required this.character,
    required this.apiClient,
    required this.novel,
    this.highlighted = false,
  });

  final CharacterItem character;
  final NovelApiClient apiClient;
  final Novel novel;
  final bool highlighted;

  CharacterAttribute? get _affiliation {
    for (final attr in character.attributes) {
      if (attr.attribute.trim().toLowerCase() == 'affiliation') return attr;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final badgeColor = character.confidence == 'high'
        ? Colors.green
        : character.confidence == 'medium'
            ? Colors.orange
            : Colors.grey;

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ExpansionTile(
        initiallyExpanded: highlighted,
        title: Row(
          children: [
            Expanded(
              child: Text(
                character.name,
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                color: badgeColor.withAlpha(30),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Text(
                displayConfidence(character.confidence),
                style: TextStyle(fontSize: 12, color: badgeColor),
              ),
            ),
            const SizedBox(width: 8),
            Chip(
              label: Text(displayFactStatus(character.reviewStatus), style: const TextStyle(fontSize: 11)),
              padding: EdgeInsets.zero,
              materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
          ],
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (character.roleType.isNotEmpty && character.roleType != 'unknown') Text(character.roleType),
            if (character.description.isNotEmpty) Text(character.description),
            if (character.aliases.isNotEmpty) Text('别名: ${character.aliases.join(', ')}'),
            if (_affiliation != null)
              _AffiliationRow(
                attribute: _affiliation!,
                apiClient: apiClient,
                novel: novel,
              ),
          ],
        ),
        children: [
          if (character.sourceChapters.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text(
                '来源章节: ${character.sourceChapters.join(', ')}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
          ..._buildCharacterDetail(context, character),
        ],
      ),
    );
  }
}

const Map<String, String> _characterAttributeLabels = <String, String>{
  'appearance': '外貌',
  'personality': '性格',
  'identity_background': '身份/背景',
  'abilities': '能力',
  'key_experiences': '重要经历',
  'affiliation': '所属势力',
};

const List<String> _characterAttributeOrder = <String>[
  'appearance',
  'personality',
  'identity_background',
  'abilities',
  'key_experiences',
  'affiliation',
];

String _characterAttributeLabel(String key) {
  final normalized = key.trim().toLowerCase();
  return _characterAttributeLabels[normalized] ?? key;
}

String _chapterRefLabel(CharacterEvidence ev) {
  final order = ev.chapterOrder > 0 ? ev.chapterOrder : (ev.chapterId > 0 ? ev.chapterId : 0);
  final title = ev.chapterTitle.trim();
  if (order > 0) {
    return title.isNotEmpty ? '第 $order 章: $title' : '第 $order 章';
  }
  return title.isNotEmpty ? title : '未标注章节';
}

List<Widget> _buildCharacterDetail(BuildContext context, CharacterItem character) {
  if (character.attributes.isNotEmpty) {
    final byKey = <String, CharacterAttribute>{};
    for (final attr in character.attributes) {
      final key = attr.attribute.trim().toLowerCase();
      if (key.isNotEmpty && !byKey.containsKey(key)) {
        byKey[key] = attr;
      }
    }
    final orderedKeys = <String>[
      ..._characterAttributeOrder.where(byKey.containsKey),
      ...byKey.keys.where((k) => !_characterAttributeOrder.contains(k)),
    ].where((k) => k != 'affiliation').toList();
    return <Widget>[
      for (final key in orderedKeys)
        _CharacterAttributeSection(attribute: byKey[key]!, label: _characterAttributeLabel(key)),
    ];
  }
  // Fallback: legacy flat data (no attributes). Show all evidence, not truncated.
  if (character.evidence.isEmpty) {
    return const <Widget>[SizedBox.shrink()];
  }
  return <Widget>[
    for (final ev in character.evidence)
      ListTile(
        dense: true,
        title: Text(
          _chapterRefLabel(ev),
          style: Theme.of(context).textTheme.bodySmall,
        ),
        subtitle: Text(
          ev.sourceQuote,
          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
        ),
      ),
  ];
}

class _CharacterAttributeSection extends StatelessWidget {
  const _CharacterAttributeSection({required this.attribute, required this.label});

  final CharacterAttribute attribute;
  final String label;

  @override
  Widget build(BuildContext context) {
    final rawValue = attribute.value.trim();
    final unmentioned = rawValue.isEmpty || rawValue == '未提及';
    final displayValue = rawValue.isEmpty ? '未提及' : attribute.value;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '$label：$displayValue',
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  color: unmentioned ? Theme.of(context).colorScheme.onSurfaceVariant : null,
                ),
          ),
          for (final ev in attribute.evidence)
            Padding(
              padding: const EdgeInsets.only(top: 4, left: 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    _chapterRefLabel(ev),
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                  Text(
                    ev.sourceQuote,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: Theme.of(context).colorScheme.onSurfaceVariant,
                        ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

List<String> _affiliationFactionNames(String value) {
  final names = <String>[];
  final segments = value.split(RegExp('[→；]'));
  for (final raw in segments) {
    final segment = raw.trim();
    if (segment.isEmpty) continue;
    final match = RegExp(r'^([^（(]+)').firstMatch(segment);
    var name = match != null ? match.group(1)!.trim() : segment;
    if (name.isEmpty) name = segment;
    if (name == '无' || name == '未提及' || name.startsWith('无（')) continue;
    if (name.isNotEmpty && !names.contains(name)) names.add(name);
  }
  return names;
}

/// 所属势力行：时间线文本 + 可点击的势力名（跳转设定页）。
class _AffiliationRow extends StatelessWidget {
  const _AffiliationRow({
    required this.attribute,
    required this.apiClient,
    required this.novel,
  });

  final CharacterAttribute attribute;
  final NovelApiClient apiClient;
  final Novel novel;

  @override
  Widget build(BuildContext context) {
    final rawValue = attribute.value.trim();
    final unmentioned = rawValue.isEmpty || rawValue == '未提及' || rawValue == '无（未提及）';
    final names = unmentioned ? <String>[] : _affiliationFactionNames(rawValue);
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (unmentioned)
            Text(
              '所属势力：无（未提及）',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            )
          else ...[
            Text(
              '所属势力：',
              style: Theme.of(context).textTheme.bodyMedium,
            ),
            Wrap(
              spacing: 8,
              runSpacing: 4,
              children: [
                for (final name in names)
                  ActionChip(
                    label: Text(name),
                    visualDensity: VisualDensity.compact,
                    onPressed: () => Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => SettingsScreen(
                          apiClient: apiClient,
                          novel: novel,
                          highlightFaction: name,
                        ),
                      ),
                    ),
                  ),
              ],
            ),
            if (rawValue.isNotEmpty)
              Text(
                rawValue,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
              ),
          ],
        ],
      ),
    );
  }
}

/// 疑似重名提示条：数据来自抽取结果的 duplicate_candidates，不做自动合并。
class _DuplicateCandidatesBanner extends StatelessWidget {
  const _DuplicateCandidatesBanner({required this.candidates});

  final List<DuplicateCandidate> candidates;

  @override
  Widget build(BuildContext context) {
    final details = candidates.map((c) => '${c.nameA} / ${c.nameB}').join('、');
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.errorContainer.withAlpha(60),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.warning_amber_rounded, size: 18, color: Theme.of(context).colorScheme.error),
          const SizedBox(width: 8),
          Expanded(
            child: Text('疑似重名 ${candidates.length} 处：$details（未自动合并，请人工核对）'),
          ),
        ],
      ),
    );
  }
}
