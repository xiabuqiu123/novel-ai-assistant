import 'dart:async';

import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';
import 'package:frontend/characters_page.dart';

/// 设定页（PRD 4.8）：世界观规则 / 势力 / 地点 / 设定事实库。
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.apiClient, required this.novel, this.highlightFaction});

  final NovelApiClient apiClient;
  final Novel novel;
  final String? highlightFaction;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  static const _categories = <String>['world_rule', 'faction', 'location', 'setting_fact'];
  static const _titles = <String>['世界观规则', '势力', '地点', '设定事实库'];

  Future<List<ExtractedFact>>? _factsFuture;
  bool _isStarting = false;
  bool _isClearingCache = false;
  int? _jobId;
  String? _jobStatus;
  int? _jobProgress;
  String? _jobError;
  Timer? _pollTimer;

  @override
  void initState() {
    super.initState();
    _factsFuture = _load();
  }

  Future<List<ExtractedFact>> _load() async {
    final all = <ExtractedFact>[];
    for (final type in _categories) {
      // E4: superseded facts belong to an older run; only show live facts.
      all.addAll(
        (await widget.apiClient.listFacts(novelId: widget.novel.id, factType: type))
            .where((fact) => fact.status != 'superseded'),
      );
    }
    return all;
  }

  void _retry() {
    setState(() { _factsFuture = _load(); });
  }

  Future<void> _startExtraction({bool forceRefresh = false}) async {
    setState(() {
      _isStarting = true;
      _jobError = null;
    });
    try {
      final result = await widget.apiClient.startSettings(
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

  Future<void> _clearSettingCache() async {
    setState(() {
      _isClearingCache = true;
    });
    try {
      final result = await widget.apiClient.clearNovelCache(widget.novel.id, taskType: 'setting_extraction');
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已清除 ${result.deletedCacheEntries} 条设定缓存')),
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('设定')),
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
                        icon: const Icon(Icons.auto_fix_high_outlined),
                        label: Text(_isStarting ? '抽取中…' : '一键抽取设定'),
                      ),
                      OutlinedButton.icon(
                        key: const Key('re-extract-settings-button'),
                        onPressed: _isStarting ? null : () => _startExtraction(forceRefresh: true),
                        icon: const Icon(Icons.refresh),
                        label: const Text('重新抽取'),
                      ),
                      OutlinedButton.icon(
                        key: const Key('clear-setting-cache-button'),
                        onPressed: _isClearingCache ? null : _clearSettingCache,
                        icon: _isClearingCache
                            ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.cleaning_services_outlined),
                        label: Text(_isClearingCache ? '清理中' : '清除设定缓存'),
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
                  child: Text('错误：$_jobError', style: TextStyle(color: Theme.of(context).colorScheme.error)),
                ),
                Expanded(
                  child: FutureBuilder<List<ExtractedFact>>(
                    future: _factsFuture,
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
                      final facts = snapshot.data ?? <ExtractedFact>[];
                      if (facts.isEmpty) {
                        return Center(
                          child: Text('暂无设定数据，点击「一键抽取设定」开始。',
                              style: Theme.of(context).textTheme.bodyMedium),
                        );
                      }
                      return RefreshIndicator(
                        onRefresh: () async => _retry(),
                        child: ListView.builder(
                          physics: const AlwaysScrollableScrollPhysics(),
                          padding: const EdgeInsets.all(16),
                          itemCount: _categories.length,
                          itemBuilder: (context, index) => _categorySection(_categories[index], _titles[index], facts),
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

  Widget _categorySection(String type, String title, List<ExtractedFact> all) {
    final items = all.where((fact) => fact.factType == type).toList();
    return ExpansionTile(
      title: Text('$title（${items.length}）'),
      initiallyExpanded: items.isNotEmpty,
      children: [
        if (type == 'faction')
          ...items.map(
            (fact) => _FactionCard(
              fact: fact,
              apiClient: widget.apiClient,
              novel: widget.novel,
              highlighted: fact == _highlightedFaction(items),
            ),
          )
        else
          ...items.map(_factTile),
      ],
    );
  }

  ExtractedFact? _highlightedFaction(List<ExtractedFact> items) {
    final target = widget.highlightFaction?.trim();
    if (target == null || target.isEmpty) return null;
    for (final fact in items) {
      final extra = fact.extra;
      final name = (extra['name'] as String? ?? '').trim();
      final aliases = extra['aliases'];
      final aliasList = aliases is List ? aliases.map((e) => e.toString()).toList() : <String>[];
      if (name == target || aliasList.contains(target)) return fact;
    }
    return null;
  }

  Widget _factTile(ExtractedFact fact) {
    final evidence = fact.evidence;
    return ListTile(
      title: Text(fact.content),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (fact.sourceQuote.isNotEmpty) Text('原文：${fact.sourceQuote}'),
          Text('置信度：${fact.confidence} · 状态：${fact.status}'),
          for (final item in evidence) ...[
            const Divider(height: 8),
            Text('证据：${item['source_quote'] ?? ''}'),
          ],
        ],
      ),
      isThreeLine: true,
    );
  }
}

List<String> _extraStringList(Object? raw) {
  if (raw is! List) return <String>[];
  return raw.map((e) => e.toString().trim()).where((e) => e.isNotEmpty).toList();
}

/// 势力卡片：名称/别名/类型/简介/上级/下属/职位/主要关系/证据。
class _FactionCard extends StatelessWidget {
  const _FactionCard({
    required this.fact,
    required this.apiClient,
    required this.novel,
    this.highlighted = false,
  });

  final ExtractedFact fact;
  final NovelApiClient apiClient;
  final Novel novel;
  final bool highlighted;

  String get _name {
    final extraName = (fact.extra['name'] as String? ?? '').trim();
    if (extraName.isNotEmpty) return extraName;
    return fact.content.split(': ').first.trim();
  }

  @override
  Widget build(BuildContext context) {
    final extra = fact.extra;
    final description = (extra['description'] as String? ?? '').trim();
    final type = (extra['type'] as String? ?? '').trim();
    final aliases = _extraStringList(extra['aliases']);
    final parent = (extra['parent'] as String? ?? '').trim();
    final subOrganizations = _extraStringList(extra['sub_organizations']);
    final positions = extra['positions'] is List ? extra['positions'] as List : const <Object?>[];
    final relationships = extra['relationships'] is List ? extra['relationships'] as List : const <Object?>[];

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ExpansionTile(
        initiallyExpanded: highlighted,
        title: Row(
          children: [
            Expanded(
              child: Text(_name, style: Theme.of(context).textTheme.titleSmall),
            ),
            if (type.isNotEmpty)
              Container(
                margin: const EdgeInsets.only(left: 8),
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(type, style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onPrimaryContainer)),
              ),
          ],
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (aliases.isNotEmpty) Text('别名：${aliases.join('、')}'),
            if (description.isNotEmpty) Text(description),
          ],
        ),
        children: [
          if (parent.isNotEmpty)
            ListTile(
              dense: true,
              title: Text('上级势力：$parent'),
            ),
          if (subOrganizations.isNotEmpty)
            ListTile(
              dense: true,
              title: Text('下属机构：${subOrganizations.join('、')}'),
            ),
          if (positions.isNotEmpty) ...[
            const Divider(height: 4),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 4),
              child: Text('职位', style: Theme.of(context).textTheme.titleSmall),
            ),
            ...positions.map(
              (position) => _FactionPositionTile(
                position: position,
                apiClient: apiClient,
                novel: novel,
              ),
            ),
          ],
          if (relationships.isNotEmpty) ...[
            const Divider(height: 4),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 4),
              child: Text('主要关系', style: Theme.of(context).textTheme.titleSmall),
            ),
            ...relationships.map(
              (relation) {
                final map = relation is Map ? Map<String, dynamic>.from(relation) : <String, dynamic>{};
                return ListTile(
                  dense: true,
                  title: Text('${map['other'] ?? ''}：${map['summary'] ?? ''}'),
                );
              },
            ),
          ],
          if (fact.sourceQuote.isNotEmpty)
            ListTile(
              dense: true,
              title: Text('原文：${fact.sourceQuote}'),
            ),
          for (final item in fact.evidence) ...[
            const Divider(height: 8),
            ListTile(
              dense: true,
              title: Text('证据：${item['source_quote'] ?? ''}'),
            ),
          ],
        ],
      ),
    );
  }
}

class _FactionPositionTile extends StatelessWidget {
  const _FactionPositionTile({
    required this.position,
    required this.apiClient,
    required this.novel,
  });

  final Object? position;
  final NovelApiClient apiClient;
  final Novel novel;

  @override
  Widget build(BuildContext context) {
    final map = position is Map ? Map<String, dynamic>.from(position as Map) : <String, dynamic>{};
    final title = (map['title'] as String? ?? '').trim();
    final holder = (map['holder'] as String? ?? '').trim();
    final holderIntro = (map['holder_intro'] as String? ?? '').trim();
    final rotation = (map['rotation'] as String? ?? '').trim();
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  '职位：${title.isEmpty ? '未提及' : title}',
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ),
              if (holder.isNotEmpty)
                InkWell(
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => CharacterProfilesScreen(
                        apiClient: apiClient,
                        novel: novel,
                        highlightCharacter: holder,
                      ),
                    ),
                  ),
                  child: Text(
                    '担任者：$holder',
                    style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                          color: Theme.of(context).colorScheme.primary,
                          fontWeight: FontWeight.w600,
                        ),
                  ),
                )
              else
                Text(
                  '担任者：未提及',
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                ),
            ],
          ),
          if (holderIntro.isNotEmpty)
            Text(
              '介绍：$holderIntro',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
          if (rotation.isNotEmpty)
            Text(
              '轮换：$rotation',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
        ],
      ),
    );
  }
}