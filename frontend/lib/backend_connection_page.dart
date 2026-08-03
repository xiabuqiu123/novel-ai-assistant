import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

import 'app_utils.dart';
import 'shared_widgets.dart';

class BackendConnectionScreen extends StatefulWidget {
  const BackendConnectionScreen({
    super.key,
    required this.apiClient,
    required this.backendBaseUrl,
    this.onBackendBaseUrlChanged,
  });

  final NovelApiClient apiClient;
  final String backendBaseUrl;
  final Future<void> Function(String value)? onBackendBaseUrlChanged;

  @override
  State<BackendConnectionScreen> createState() => _BackendConnectionScreenState();
}

class _BackendConnectionScreenState extends State<BackendConnectionScreen> {
  final _controller = TextEditingController();
  bool _isSaving = false;
  bool _isTesting = false;
  String? _message;
  String? _error;

  @override
  void initState() {
    super.initState();
    _controller.text = widget.backendBaseUrl;
  }

  @override
  void didUpdateWidget(covariant BackendConnectionScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.backendBaseUrl != widget.backendBaseUrl && _controller.text.trim() == oldWidget.backendBaseUrl) {
      _controller.text = widget.backendBaseUrl;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final normalized = normalizeBackendBaseUrl(_controller.text);
    setState(() {
      _isSaving = true;
      _message = null;
      _error = null;
    });
    try {
      await widget.onBackendBaseUrlChanged?.call(normalized);
      if (!mounted) return;
      _controller.text = normalized;
      setState(() => _message = '后端地址已保存, 可返回书架或再次测试连接。');
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _isSaving = false);
    }
  }

  Future<void> _test() async {
    final normalized = normalizeBackendBaseUrl(_controller.text);
    final client = NovelApiClient(baseUrl: normalized);
    setState(() {
      _isTesting = true;
      _message = null;
      _error = null;
    });
    try {
      final health = await client.health();
      if (!mounted) return;
      setState(() => _message = '后端可连接: ${health['status'] ?? 'ok'}');
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = backendConnectionTroubleshootingMessage(normalized, error));
    } finally {
      if (mounted) setState(() => _isTesting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final canSave = widget.onBackendBaseUrlChanged != null;
    return Scaffold(
      appBar: AppBar(title: const Text('后端连接')),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 720),
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text('当前后端: ${widget.backendBaseUrl}', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 12),
                TextFormField(
                  key: const Key('backend-url-field'),
                  controller: _controller,
                  decoration: const InputDecoration(
                    labelText: '后端地址',
                    helperText: '真机请使用 http://电脑IP:8000, 安卓模拟器可使用 http://10.0.2.2:8000。',
                    border: OutlineInputBorder(),
                  ),
                  keyboardType: TextInputType.url,
                ),
                const SizedBox(height: 12),
                const Text('这是本地 FastAPI 后端地址; 模型 API 服务商地址请在「模型设置」中单独配置。'),
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: FilledButton.icon(
                        key: const Key('save-backend-url-button'),
                        onPressed: canSave && !_isSaving ? _save : null,
                        icon: _isSaving
                            ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.save_outlined),
                        label: const Text('保存'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: OutlinedButton.icon(
                        key: const Key('test-backend-url-button'),
                        onPressed: _isTesting ? null : _test,
                        icon: _isTesting
                            ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.wifi_tethering_outlined),
                        label: const Text('测试连接'),
                      ),
                    ),
                  ],
                ),
                if (_message != null) ...[
                  const SizedBox(height: 16),
                  Text(_message!, style: TextStyle(color: Theme.of(context).colorScheme.primary)),
                ],
                if (_error != null) ...[
                  const SizedBox(height: 16),
                  ErrorState(message: _error!, onRetry: _test),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
