import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

import 'analysis_jobs_page.dart';
import 'backend_connection_page.dart';
import 'bookshelf_page.dart';
import 'import_page.dart';
import 'model_settings_page.dart';

class HomeScreen extends StatelessWidget {
  const HomeScreen({
    super.key,
    required this.apiClient,
    required this.backendBaseUrl,
    this.onBackendBaseUrlChanged,
  });

  final NovelApiClient apiClient;
  final String backendBaseUrl;
  final Future<void> Function(String value)? onBackendBaseUrlChanged;

  @override
  Widget build(BuildContext context) {
    final entries = <HomeEntry>[
      HomeEntry(
        title: '后端连接',
        icon: Icons.settings_ethernet_outlined,
        destination: BackendConnectionScreen(
          apiClient: apiClient,
          backendBaseUrl: backendBaseUrl,
          onBackendBaseUrlChanged: onBackendBaseUrlChanged,
        ),
      ),
      HomeEntry(
        title: '书架',
        icon: Icons.library_books_outlined,
        destination: BookshelfScreen(apiClient: apiClient),
      ),
      HomeEntry(
        title: '导入 TXT',
        icon: Icons.upload_file_outlined,
        destination: ImportTxtScreen(apiClient: apiClient),
      ),
      HomeEntry(
        title: '模型设置',
        icon: Icons.tune_outlined,
        destination: ModelSettingsScreen(apiClient: apiClient),
      ),
      HomeEntry(
        title: '分析任务',
        icon: Icons.monitor_heart_outlined,
        destination: AnalysisJobsScreen(apiClient: apiClient),
      ),
    ];

    return Scaffold(
      appBar: AppBar(
        title: const Text('书镜辨章'),
      ),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 720),
            child: ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: entries.length,
              separatorBuilder: (context, index) => const SizedBox(height: 12),
              itemBuilder: (context, index) {
                return HomeEntryTile(entry: entries[index]);
              },
            ),
          ),
        ),
      ),
    );
  }
}

class HomeEntryTile extends StatelessWidget {
  const HomeEntryTile({super.key, required this.entry});

  final HomeEntry entry;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      child: ListTile(
        key: Key('home-entry-${entry.title}'),
        minTileHeight: 72,
        leading: Icon(entry.icon),
        title: Text(entry.title),
        trailing: const Icon(Icons.chevron_right),
        onTap: () {
          Navigator.of(context).push(
            MaterialPageRoute<void>(builder: (context) => entry.destination),
          );
        },
      ),
    );
  }
}

class HomeEntry {
  const HomeEntry({
    required this.title,
    required this.icon,
    required this.destination,
  });

  final String title;
  final IconData icon;
  final Widget destination;
}
