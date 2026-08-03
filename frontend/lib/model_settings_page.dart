import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

import 'shared_widgets.dart';

class ModelSettingsScreen extends StatefulWidget {
  const ModelSettingsScreen({super.key, required this.apiClient});

  final NovelApiClient apiClient;

  @override
  State<ModelSettingsScreen> createState() => _ModelSettingsScreenState();
}

class _ModelSettingsScreenState extends State<ModelSettingsScreen> {
  final _formKey = GlobalKey<FormState>();
  final _apiKeyController = TextEditingController();
  final _baseUrlController = TextEditingController();
  final _modelController = TextEditingController();
  Future<ModelSettings>? _settingsFuture;
  bool _apiKeySet = false;
  bool _isSaving = false;
  bool _saved = false;
  String? _saveError;
  String _provider = '自定义';
  bool _isTesting = false;
  String? _testResult;
  String? _testError;

  static const _providers = <String, Map<String, String>>{
    'OpenAI': {'baseUrl': 'https://api.openai.com/v1', 'model': 'gpt-4.1-mini'},
    'DeepSeek': {'baseUrl': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat'},
    '自定义': {'baseUrl': '', 'model': 'gpt-4.1-mini'},
  };

  void _onProviderChanged(String? provider) {
    if (provider == null) return;
    setState(() {
      _provider = provider;
      final preset = _providers[provider];
      if (preset != null) {
        _baseUrlController.text = preset['baseUrl']!;
        _modelController.text = preset['model']!;
      }
    });
  }

  Future<void> _testConnection() async {
    setState(() {
      _isTesting = true;
      _testResult = null;
      _testError = null;
    });
    try {
      final apiKey = _apiKeyController.text.trim();
      final baseUrl = _baseUrlController.text.trim();
      if (apiKey.isEmpty) {
        setState(() {
          _testError = 'API Key 为空';
        });
        return;
      }
      if (baseUrl.isEmpty) {
        setState(() {
          _testError = 'Base URL 为空';
        });
        return;
      }
      final result = await widget.apiClient.testConnection(
        apiKey: apiKey,
        baseUrl: baseUrl,
        model: _modelController.text.trim(),
      );
      if (!mounted) return;
      setState(() {
        if (result.ok) {
          _testResult = result.displayMessage;
        } else {
          _testError = result.displayMessage;
        }
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _testError = error.toString();
      });
    } finally {
      if (mounted) {
        setState(() {
          _isTesting = false;
        });
      }
    }
  }

  @override
  void initState() {
    super.initState();
    _settingsFuture = _loadSettings();
  }

  @override
  void dispose() {
    _apiKeyController.dispose();
    _baseUrlController.dispose();
    _modelController.dispose();
    super.dispose();
  }

  Future<ModelSettings> _loadSettings() async {
    final settings = await widget.apiClient.getModelSettings();
    _apiKeySet = settings.apiKeySet;
    _baseUrlController.text = settings.baseUrl;
    _modelController.text = settings.model;
    return settings;
  }

  void _retryLoad() {
    setState(() {
      _saveError = null;
      _saved = false;
      _settingsFuture = _loadSettings();
    });
  }

  Future<void> _save() async {
    final form = _formKey.currentState;
    if (form == null || !form.validate()) {
      return;
    }
    final apiKey = _apiKeyController.text.trim();
    setState(() {
      _isSaving = true;
      _saved = false;
      _saveError = null;
    });
    try {
      await widget.apiClient.saveModelSettings(
        apiKey: apiKey,
        baseUrl: _baseUrlController.text.trim(),
        model: _modelController.text.trim(),
      );
      if (!mounted) return;
      _apiKeyController.clear();
      setState(() {
        _apiKeySet = apiKey.isNotEmpty;
        _saved = true;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _saveError = error.toString();
      });
    } finally {
      if (mounted) {
        setState(() {
          _isSaving = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('模型设置')),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 720),
            child: FutureBuilder<ModelSettings>(
              future: _settingsFuture,
              builder: (context, snapshot) {
                final isLoading = snapshot.connectionState == ConnectionState.waiting;
                final loadError = snapshot.error;
                return ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    if (isLoading)
                      const LinearProgressIndicator()
                    else if (loadError != null)
                      ErrorState(message: loadError.toString(), onRetry: _retryLoad)
                    else
                      ModelSettingsForm(
                        formKey: _formKey,
                        apiKeyController: _apiKeyController,
                        baseUrlController: _baseUrlController,
                        modelController: _modelController,
                        apiKeySet: _apiKeySet,
                        isSaving: _isSaving,
                        saved: _saved,
                        saveError: _saveError,
                        onSave: _save,
                        provider: _provider,
                        onProviderChanged: _onProviderChanged,
                        providers: _providers,
                        onTestConnection: _testConnection,
                        isTesting: _isTesting,
                        testResult: _testResult,
                        testError: _testError,
                      ),
                    const SizedBox(height: 16),
                    _UsageStatsCard(apiClient: widget.apiClient),
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

class _UsageStatsCard extends StatefulWidget {
  const _UsageStatsCard({required this.apiClient});

  final NovelApiClient apiClient;

  @override
  State<_UsageStatsCard> createState() => _UsageStatsCardState();
}

class _UsageStatsCardState extends State<_UsageStatsCard> {
  late Future<UsageStats> _future;

  @override
  void initState() {
    super.initState();
    _future = widget.apiClient.fetchUsageStats();
  }

  void _retry() {
    setState(() {
      _future = widget.apiClient.fetchUsageStats();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: FutureBuilder<UsageStats>(
          future: _future,
          builder: (context, snapshot) {
            if (snapshot.connectionState == ConnectionState.waiting) {
              return const Row(
                children: [
                  SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                  SizedBox(width: 12),
                  Text('正在读取累计调用统计…'),
                ],
              );
            }
            if (snapshot.hasError) {
              return Row(
                children: [
                  Expanded(child: Text('累计调用统计读取失败: ${snapshot.error}')),
                  TextButton(onPressed: _retry, child: const Text('重试')),
                ],
              );
            }
            final stats = snapshot.data;
            if (stats == null) {
              return const SizedBox.shrink();
            }
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('累计调用统计', style: TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                Text('累计模型调用（成功缓存）: ${stats.modelCallsSucceeded} 次'),
                Text('累计模型调用（含失败尝试）: ${stats.modelCallsAttempted} 次'),
                Text('本地兜底结果: ${stats.localFallbackResults} 条'),
                Text('失败任务数: ${stats.failedJobs}'),
                Text('缓存条目数: ${stats.cacheEntries}'),
                const SizedBox(height: 4),
                const Text(
                  '注：按当前缓存统计，清除缓存后计数会相应减少；token 用量暂未采集，本版本不显示。',
                  style: TextStyle(fontSize: 12, color: Colors.grey),
                ),
              ],
            );
          },
        ),
      ),
    );
  }
}

class ModelSettingsForm extends StatelessWidget {
  const ModelSettingsForm({
    super.key,
    required this.formKey,
    required this.apiKeyController,
    required this.baseUrlController,
    required this.modelController,
    required this.apiKeySet,
    required this.isSaving,
    required this.saved,
    required this.saveError,
    required this.onSave,
    required this.provider,
    required this.onProviderChanged,
    required this.providers,
    required this.onTestConnection,
    required this.isTesting,
    this.testResult,
    this.testError,
  });

  final GlobalKey<FormState> formKey;
  final TextEditingController apiKeyController;
  final TextEditingController baseUrlController;
  final TextEditingController modelController;
  final bool apiKeySet;
  final bool isSaving;
  final bool saved;
  final String? saveError;
  final VoidCallback onSave;
  final String provider;
  final ValueChanged<String?> onProviderChanged;
  final Map<String, Map<String, String>> providers;
  final VoidCallback onTestConnection;
  final bool isTesting;
  final String? testResult;
  final String? testError;

  @override
  Widget build(BuildContext context) {
    return Form(
      key: formKey,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            'API Key 已设置: ${apiKeySet ? '是' : '否'}',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 12),
          TextFormField(
            key: const Key('settings-api-key-field'),
            controller: apiKeyController,
            decoration: const InputDecoration(
              labelText: 'API Key',
              helperText: '留空则不保存 API Key',
              border: OutlineInputBorder(),
            ),
            obscureText: true,
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            initialValue: provider,
            decoration: const InputDecoration(
              labelText: '服务商',
              border: OutlineInputBorder(),
            ),
            items: providers.keys.map((name) {
              return DropdownMenuItem<String>(
                value: name,
                child: Text(name),
              );
            }).toList(),
            onChanged: onProviderChanged,
          ),
          const SizedBox(height: 12),
          TextFormField(
            key: const Key('settings-base-url-field'),
            controller: baseUrlController,
            decoration: const InputDecoration(
              labelText: '模型 API 地址',
              helperText: 'OpenAI 兼容服务商地址, 不是本地后端地址。',
              border: OutlineInputBorder(),
            ),
            keyboardType: TextInputType.url,
          ),
          const SizedBox(height: 12),
          TextFormField(
            key: const Key('settings-model-field'),
            controller: modelController,
            decoration: const InputDecoration(
              labelText: '模型',
              border: OutlineInputBorder(),
            ),
            validator: (value) {
              if (value == null || value.trim().isEmpty) {
                return '请输入模型名称';
              }
              return null;
            },
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            key: const Key('settings-save-button'),
            onPressed: isSaving ? null : onSave,
            icon: isSaving
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.save_outlined),
            label: Text(isSaving ? '保存中' : '保存'),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            key: const Key('settings-test-connection-button'),
            onPressed: isTesting ? null : onTestConnection,
            icon: isTesting
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.wifi_find_outlined),
            label: Text(isTesting ? '测试中' : '测试连接'),
          ),
          if (testResult != null) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                const Icon(Icons.check_circle, color: Colors.green, size: 18),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    testResult!,
                    style: const TextStyle(color: Colors.green, fontSize: 13),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            const Text(
              '连接测试仅验证本次请求; 真实分析是否走服务商或缓存, 以分析结果面板为准。',
              style: TextStyle(fontSize: 12),
            ),
          ],
          if (testError != null) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                const Icon(Icons.error, color: Colors.red, size: 18),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    testError!,
                    style: const TextStyle(color: Colors.red, fontSize: 13),
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ],
          if (saved) ...[
            const SizedBox(height: 12),
            const Text('设置已保存'),
          ],
          if (saveError != null) ...[
            const SizedBox(height: 12),
            ErrorState(message: saveError!, onRetry: onSave),
          ],
        ],
      ),
    );
  }
}
