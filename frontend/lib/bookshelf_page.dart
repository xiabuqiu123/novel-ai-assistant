import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

import 'chapters_page.dart';
import 'shared_widgets.dart';

class BookshelfScreen extends StatefulWidget {
  const BookshelfScreen({super.key, required this.apiClient});

  final NovelApiClient apiClient;

  @override
  State<BookshelfScreen> createState() => _BookshelfScreenState();
}

class _BookshelfScreenState extends State<BookshelfScreen> {
  Future<BookshelfState>? _stateFuture;

  @override
  void initState() {
    super.initState();
    _stateFuture = _load();
  }

  Future<BookshelfState> _load() async {
    final health = await widget.apiClient.health();
    final novels = await widget.apiClient.listNovels();
    return BookshelfState(
      backendStatus: health['status'] as String? ?? 'unknown',
      novels: novels,
    );
  }

  void _retry() {
    setState(() {
      _stateFuture = _load();
    });
  }

  Future<void> _confirmDelete(Novel novel) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('删除小说?'),
        content: Text('将删除「${novel.title}」及其解析章节、分析任务和相关缓存, 确定删除?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      final result = await widget.apiClient.deleteNovel(novel.id);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已删除 ${result.title}')),
      );
      _retry();
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('删除失败: $error')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('书架')),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: FutureBuilder<BookshelfState>(
              future: _stateFuture,
              builder: (context, snapshot) {
                final isLoading = snapshot.connectionState == ConnectionState.waiting;
                final error = snapshot.error;
                final state = snapshot.data;

                return RefreshIndicator(
                  onRefresh: () async => _retry(),
                  child: ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.all(16),
                    children: [
                      BackendStatusPanel(
                        isLoading: isLoading,
                        status: state?.backendStatus,
                        error: error,
                        onRetry: _retry,
                      ),
                      const SizedBox(height: 16),
                      Text('我的小说', style: Theme.of(context).textTheme.titleLarge),
                      const SizedBox(height: 8),
                      if (isLoading)
                        const LinearProgressIndicator()
                      else if (error != null)
                        ErrorState(message: error.toString(), onRetry: _retry)
                      else if (state == null || state.novels.isEmpty)
                        const EmptyBookshelfState()
                      else
                        ...state.novels.map(
                          (novel) => NovelListTile(
                            novel: novel,
                            onDelete: () => _confirmDelete(novel),
                            onTap: () {
                              Navigator.of(context).push(
                                MaterialPageRoute<void>(
                                  builder: (context) => ChapterListScreen(
                                    apiClient: widget.apiClient,
                                    novel: novel,
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

class BookshelfState {
  const BookshelfState({required this.backendStatus, required this.novels});

  final String backendStatus;
  final List<Novel> novels;
}

class BackendStatusPanel extends StatelessWidget {
  const BackendStatusPanel({
    super.key,
    required this.isLoading,
    required this.status,
    required this.error,
    required this.onRetry,
  });

  final bool isLoading;
  final String? status;
  final Object? error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final hasError = error != null;
    final icon = hasError
        ? Icons.cloud_off_outlined
        : isLoading
            ? Icons.sync_outlined
            : Icons.cloud_done_outlined;
    final label = hasError
        ? '后端不可用'
        : isLoading
            ? '正在检查后端'
            : '后端状态: ${status ?? '未知'}';

    return Card(
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            Icon(icon, color: hasError ? colorScheme.error : colorScheme.primary),
            const SizedBox(width: 12),
            Expanded(
              child: Text(label, style: Theme.of(context).textTheme.titleMedium),
            ),
            SizedBox(
              width: 48,
              height: 48,
              child: IconButton(
                tooltip: '重试',
                onPressed: isLoading ? null : onRetry,
                icon: const Icon(Icons.refresh),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class NovelListTile extends StatelessWidget {
  const NovelListTile({super.key, required this.novel, required this.onTap, required this.onDelete});

  final Novel novel;
  final VoidCallback onTap;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final subtitle = '${novel.chapterCount} 章, ${novel.chunkCount} 个分块';
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      child: ListTile(
        leading: const Icon(Icons.menu_book_outlined),
        title: Text(novel.title),
        subtitle: Text(novel.encoding.isEmpty ? subtitle : '$subtitle, ${novel.encoding}'),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            IconButton(
              key: Key('delete-novel-${novel.id}'),
              tooltip: '删除小说',
              onPressed: onDelete,
              icon: const Icon(Icons.delete_outline),
            ),
            const Icon(Icons.chevron_right),
          ],
        ),
        onTap: onTap,
      ),
    );
  }
}
