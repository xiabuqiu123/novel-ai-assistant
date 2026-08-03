import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

import 'app_utils.dart';
import 'backend_base_url_store.dart';
import 'home_page.dart';

export 'analysis_jobs_page.dart';
export 'app_utils.dart';
export 'backend_connection_page.dart';
export 'bookshelf_page.dart';
export 'chapter_reader_page.dart';
export 'chapters_page.dart';
export 'export_report_page.dart';
export 'characters_page.dart';
export 'home_page.dart';
export 'import_page.dart';
export 'model_settings_page.dart';
export 'qa_page.dart';
export 'relationship_graph_page.dart';
export 'shared_widgets.dart';

void main() {
  runApp(const NovelAnalysisApp());
}

class NovelAnalysisApp extends StatefulWidget {
  const NovelAnalysisApp({super.key, this.apiClient});

  final NovelApiClient? apiClient;

  @override
  State<NovelAnalysisApp> createState() => _NovelAnalysisAppState();
}

class _NovelAnalysisAppState extends State<NovelAnalysisApp> {
  // null 表示尚未从本地存储加载完成（展示加载态）。
  String? _backendBaseUrl;
  NovelApiClient? _client;

  NovelApiClient get _effectiveClient =>
      widget.apiClient ?? (_client ??= NovelApiClient(baseUrl: _backendBaseUrl ?? defaultBackendBaseUrl));

  @override
  void initState() {
    super.initState();
    _loadBackendBaseUrl();
  }

  Future<void> _loadBackendBaseUrl() async {
    // 测试注入的 apiClient 跳过持久化，避免依赖 SharedPreferences 宿主。
    if (widget.apiClient != null) return;
    final loaded = await BackendBaseUrlStore.load();
    if (!mounted) return;
    setState(() {
      _backendBaseUrl = loaded;
      _client = NovelApiClient(baseUrl: loaded);
    });
  }

  Future<void> _updateBackendBaseUrl(String value) async {
    if (widget.apiClient != null) return;
    // 规范化后写入本地存储，重启后仍生效——这正是修复真机反复配置的根因所在。
    final normalized = await BackendBaseUrlStore.save(value);
    if (!mounted) return;
    setState(() {
      _backendBaseUrl = normalized;
      _client = NovelApiClient(baseUrl: normalized);
    });
  }

  @override
  Widget build(BuildContext context) {
    final baseUrl = _backendBaseUrl;
    if (widget.apiClient == null && baseUrl == null) {
      return MaterialApp(
        debugShowCheckedModeBanner: false,
        title: '加载中',
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF2563EB)),
          useMaterial3: true,
          fontFamily: 'Microsoft YaHei',
          fontFamilyFallback: <String>['Microsoft YaHei', 'DengXian', 'Noto Sans CJK SC', 'sans-serif'],
        ),
        home: const Scaffold(
          body: Center(child: CircularProgressIndicator()),
        ),
      );
    }
    final client = _effectiveClient;
    return MaterialApp(
      title: '书镜辨章',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF2563EB),
          brightness: Brightness.light,
        ),
        useMaterial3: true,
        fontFamily: 'Microsoft YaHei',
        fontFamilyFallback: <String>['Microsoft YaHei', 'DengXian', 'Noto Sans CJK SC', 'sans-serif'],
        appBarTheme: const AppBarTheme(
          centerTitle: false,
          scrolledUnderElevation: 2,
        ),
        cardTheme: CardThemeData(
          clipBehavior: Clip.antiAlias,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
        ),
        snackBarTheme: const SnackBarThemeData(
          behavior: SnackBarBehavior.floating,
        ),
        visualDensity: VisualDensity.adaptivePlatformDensity,
      ),
      darkTheme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF2563EB),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
        fontFamily: 'Microsoft YaHei',
        fontFamilyFallback: <String>['Microsoft YaHei', 'DengXian', 'Noto Sans CJK SC', 'sans-serif'],
        appBarTheme: const AppBarTheme(
          centerTitle: false,
          scrolledUnderElevation: 2,
        ),
        cardTheme: CardThemeData(
          clipBehavior: Clip.antiAlias,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
        ),
        snackBarTheme: const SnackBarThemeData(
          behavior: SnackBarBehavior.floating,
        ),
        visualDensity: VisualDensity.adaptivePlatformDensity,
      ),
      home: HomeScreen(
        apiClient: client,
        backendBaseUrl: client.baseUrl,
        onBackendBaseUrlChanged: widget.apiClient == null ? _updateBackendBaseUrl : null,
      ),
    );
  }
}