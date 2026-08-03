import 'package:flutter/material.dart';

import 'package:file_picker/file_picker.dart';

import 'package:frontend/api_client.dart';

import 'bookshelf_page.dart';
import 'shared_widgets.dart';

class ImportTxtScreen extends StatefulWidget {
  const ImportTxtScreen({super.key, required this.apiClient});

  final NovelApiClient apiClient;

  @override
  State<ImportTxtScreen> createState() => _ImportTxtScreenState();
}

class _ImportTxtScreenState extends State<ImportTxtScreen> {
  final _formKey = GlobalKey<FormState>();
  final _titleController = TextEditingController();
  final _textController = TextEditingController();
  bool _isSubmitting = false;
  ImportTxtResult? _result;
  String? _error;
  String? _pickedFileName;
  List<int>? _pickedFileBytes;

  @override
  void dispose() {
    _titleController.dispose();
    _textController.dispose();
    super.dispose();
  }

  Future<void> _pickFile() async {
    try {
      final result = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: const ['txt'],
        withData: true,
      );
      if (!mounted || result == null || result.files.isEmpty) {
        return;
      }
      final file = result.files.first;
      final bytes = file.bytes;
      if (bytes == null || bytes.isEmpty) {
        setState(() {
          _error = '无法读取所选文件。';
        });
        return;
      }
      setState(() {
        _pickedFileName = file.name;
        _pickedFileBytes = bytes;
        _error = null;
        _result = null;
        if (_titleController.text.trim().isEmpty) {
          _titleController.text = file.name.replaceAll(
            RegExp(r'\.txt$', caseSensitive: false),
            '',
          );
        }
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = '文件选择器不可用: $error';
      });
    }
  }

  void _clearPickedFile() {
    setState(() {
      _pickedFileName = null;
      _pickedFileBytes = null;
    });
  }
  Future<void> _submit() async {
    final form = _formKey.currentState;
    if (form == null || !form.validate()) {
      return;
    }
    setState(() {
      _isSubmitting = true;
      _error = null;
      _result = null;
    });
    try {
      final bytes = _pickedFileBytes;
      final result = bytes != null
          ? await widget.apiClient.importTxtFile(
              title: _titleController.text.trim(),
              filename: _pickedFileName ?? 'novel.txt',
              bytes: bytes,
            )
          : await widget.apiClient.importTxt(
              title: _titleController.text.trim(),
              text: _textController.text,
            );
      if (!mounted) return;
      setState(() {
        _result = result;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.toString();
      });
    } finally {
      if (mounted) {
        setState(() {
          _isSubmitting = false;
        });
      }
    }
  }

  void _openBookshelf() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (context) => BookshelfScreen(apiClient: widget.apiClient),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('导入 TXT')),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 840),
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Form(
                  key: _formKey,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      OutlinedButton.icon(
                        key: const Key('import-file-button'),
                        onPressed: _isSubmitting ? null : _pickFile,
                        icon: const Icon(Icons.folder_open_outlined),
                        label: const Text('选择 TXT 文件'),
                      ),
                      if (_pickedFileName != null) ...[
                        const SizedBox(height: 8),
                        Card(
                          child: ListTile(
                            key: const Key('import-picked-file-card'),
                            leading: const Icon(Icons.description_outlined),
                            title: Text(_pickedFileName!),
                            subtitle: Text(
                              '${((_pickedFileBytes?.length ?? 0) / 1024).toStringAsFixed(1)} KB, 按原样导入, 自动识别编码',
                            ),
                            trailing: IconButton(
                              key: const Key('import-clear-file-button'),
                              icon: const Icon(Icons.close),
                              onPressed: _isSubmitting ? null : _clearPickedFile,
                            ),
                          ),
                        ),
                      ],
                      const SizedBox(height: 12),
                      TextFormField(
                        key: const Key('import-title-field'),
                        controller: _titleController,
                        decoration: const InputDecoration(
                          labelText: '标题',
                          border: OutlineInputBorder(),
                        ),
                        textInputAction: TextInputAction.next,
                        validator: (value) {
                          if (value == null || value.trim().isEmpty) {
                            return '请输入标题';
                          }
                          return null;
                        },
                      ),
                      const SizedBox(height: 12),
                      TextFormField(
                        key: const Key('import-text-field'),
                        controller: _textController,
                        decoration: const InputDecoration(
                          labelText: 'TXT 内容',
                          alignLabelWithHint: true,
                          border: OutlineInputBorder(),
                        ),
                        minLines: 10,
                        maxLines: 18,
                        validator: (value) {
                          if (value == null || value.trim().isEmpty) {
                            if (_pickedFileBytes != null) {
                              return null;
                            }
                            return '请粘贴 TXT 内容或选择文件';
                          }
                          return null;
                        },
                      ),
                      const SizedBox(height: 8),
                      const Text('分块大小: 6000 字符'),
                      const SizedBox(height: 16),
                      FilledButton.icon(
                        key: const Key('import-submit-button'),
                        onPressed: _isSubmitting ? null : _submit,
                        icon: _isSubmitting
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.upload_file_outlined),
                        label: Text(_isSubmitting ? '导入中' : '开始导入'),
                      ),
                    ],
                  ),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 16),
                  ErrorState(message: _error!, onRetry: _submit),
                ],
                if (_result != null) ...[
                  const SizedBox(height: 16),
                  ImportResultPanel(result: _result!, onOpenBookshelf: _openBookshelf),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class ImportResultPanel extends StatelessWidget {
  const ImportResultPanel({super.key, required this.result, required this.onOpenBookshelf});

  final ImportTxtResult result;
  final VoidCallback onOpenBookshelf;

  @override
  Widget build(BuildContext context) {
    final status = result.imported ? '导入成功' : '已导入过';
    final duplicate = result.duplicateOf;
    return Card(
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.check_circle_outline, color: Theme.of(context).colorScheme.primary),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(status, style: Theme.of(context).textTheme.titleMedium),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text('小说 ID: ${result.id}'),
            Text('标题: ${result.title}'),
            if (!result.imported && duplicate != null) ...[
              const SizedBox(height: 8),
              Text('相同内容已存在: ${duplicate.title}'),
              if (duplicate.encoding.isNotEmpty) Text('已有编码: ${duplicate.encoding}'),
              if (result.requestedTitle.isNotEmpty) Text('请求标题: ${result.requestedTitle}'),
              if (result.requestedSourceFilename.isNotEmpty)
                Text('上传文件名: ${result.requestedSourceFilename}'),
            ],
            Text('章节数: ${result.chapterCount}'),
            Text('分块数: ${result.chunkCount}'),
            Text('编码: ${result.encoding}'),
            const SizedBox(height: 12),
            FilledButton.icon(
              key: const Key('view-bookshelf-button'),
              onPressed: onOpenBookshelf,
              icon: const Icon(Icons.library_books_outlined),
              label: const Text('查看书架'),
            ),
          ],
        ),
      ),
    );
  }
}
