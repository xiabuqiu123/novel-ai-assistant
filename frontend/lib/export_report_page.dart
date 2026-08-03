import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:frontend/api_client.dart';

import 'shared_widgets.dart';

/// 导出报告页（PRD 第 12 页 / 9.h；A8：保存 Markdown 到自选位置，预览截断）。
///
/// 一键导出合并报告：全书大纲 + 人物档案 + 人物关系 + 设定（规则/势力/地点/事实）
/// + 事件时间线 + 设定冲突（含复核结论）。可选是否附带章节原文。
class ExportReportScreen extends StatefulWidget {
  const ExportReportScreen({
    super.key,
    required this.apiClient,
    required this.novel,
    this.savePathPicker,
    this.fileWriter,
  });

  final NovelApiClient apiClient;
  final Novel novel;

  /// 可注入的“选择保存路径”回调，返回 null 表示用户取消。widget test 注入临时路径避免真弹对话框。
  final Future<String?> Function(String filename)? savePathPicker;

  /// 可注入的“写盘”回调，默认用 dart:io 把 markdown 写入用户选择的路径。widget test 注入内存记录器避免真磁盘 IO（fake-async 下真实 File IO 不上 pump 调度，会挂起）。
  final Future<void> Function(String path, String markdown)? fileWriter;

  @override
  State<ExportReportScreen> createState() => _ExportReportScreenState();
}

class _ExportReportScreenState extends State<ExportReportScreen> {
  bool _includeChapters = true;
  bool _isExporting = false;
  MarkdownExportResult? _exportResult;
  String? _exportError;

  Future<void> _generateAndSave() async {
    setState(() {
      _isExporting = true;
      _exportError = null;
      _exportResult = null;
    });
    try {
      final data = await widget.apiClient.exportReport(widget.novel.id,
          includeChapters: _includeChapters);
      if (!mounted) return;
      final result = MarkdownExportResult.fromJson(data);
      setState(() {
        _exportResult = result;
      });
      // 保存到用户选择的路径。null 表示用户在对话框中取消，保留预览不算错误。
      final path = await (widget.savePathPicker ?? _defaultPickSavePath)(result.filename);
      if (path == null) return;
      await (widget.fileWriter ?? _writeFile)(path, result.markdown);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('已保存到: $path')),
      );
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _exportError = error.toString();
      });
    } finally {
      if (mounted) {
        setState(() {
          _isExporting = false;
        });
      }
    }
  }

  Future<String?> _defaultPickSavePath(String filename) async {
    final path = await FilePicker.platform.saveFile(
      dialogTitle: '保存分析报告',
      fileName: filename,
    );
    return path;
  }

  Future<void> _writeFile(String path, String markdown) async {
    await File(path).writeAsString(markdown);
  }

  Future<void> _copyToClipboard() async {
    final markdown = _exportResult?.markdown ?? '';
    if (markdown.isEmpty) return;
    await Clipboard.setData(ClipboardData(text: markdown));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('已复制报告到剪贴板')),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('导出报告'),
        actions: [
          IconButton(
            key: const Key('copy-report-button'),
            icon: const Icon(Icons.copy_outlined),
            tooltip: '复制报告',
            onPressed: (_exportResult?.markdown.isNotEmpty ?? false) ? _copyToClipboard : null,
          ),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text('《${widget.novel.title}》', style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 8),
                const Text(
                  '合并报告包含：全书大纲、人物档案、人物关系、设定（规则/势力/地点/事实）、事件时间线、设定冲突（含复核结论）。',
                ),
                const SizedBox(height: 16),
                SwitchListTile(
                  key: const Key('include-chapters-switch'),
                  contentPadding: EdgeInsets.zero,
                  title: const Text('附带章节原文'),
                  subtitle: const Text('关闭后仅导出分析结论与证据，体积更小。'),
                  value: _includeChapters,
                  onChanged: _isExporting ? null : (value) => setState(() => _includeChapters = value),
                ),
                const SizedBox(height: 8),
                FilledButton.icon(
                  key: const Key('export-report-button'),
                  onPressed: _isExporting ? null : _generateAndSave,
                  icon: _isExporting
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.save_outlined),
                  label: Text(_isExporting ? '导出中' : '保存 Markdown 到…'),
                ),
                const SizedBox(height: 16),
                if (_exportError != null)
                  ErrorState(message: _exportError!, onRetry: _generateAndSave),
                if (_exportResult != null) MarkdownExportPanel(result: _exportResult!),
              ],
            ),
          ),
        ),
      ),
    );
  }
}