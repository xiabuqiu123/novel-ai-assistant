import 'backend_base_url.dart';

String get defaultBackendBaseUrl => getDefaultBackendBaseUrl();

String normalizeBackendBaseUrl(String value) {
  var normalized = value.trim();
  if (normalized.isEmpty) return defaultBackendBaseUrl;
  if (!normalized.startsWith('http://') && !normalized.startsWith('https://')) {
    normalized = 'http://$normalized';
  }
  while (normalized.endsWith('/')) {
    normalized = normalized.substring(0, normalized.length - 1);
  }
  return normalized;
}

String backendConnectionTroubleshootingMessage(String backendUrl, Object error) {
  final normalized = normalizeBackendBaseUrl(backendUrl);
  final host = Uri.tryParse(normalized)?.host ?? '';
  final hints = <String>[
    '已测试后端: $normalized',
    '请在项目根目录运行 .\\run_backend.ps1 启动后端, 或使用 uvicorn --host 0.0.0.0 --port 8000 启动。',
    '请保持手机与电脑在同一 Wi-Fi/局域网, 并使用电脑的局域网 IPv4 地址, 例如 http://192.168.1.162:8000。',
    '若仍无法连接, 请在 Windows 防火墙中放行 TCP 8000 端口的入站连接。',
    '「模型设置」中的模型 API 地址是独立配置, 应保持指向 OpenAI 兼容服务商。',
  ];
  if (host == '10.0.2.2') {
    hints.insert(1, '10.0.2.2 仅适用于安卓模拟器, 真机必须使用电脑的局域网 IPv4 地址。');
  } else if (host == '127.0.0.1' || host == 'localhost') {
    hints.insert(1, '真机上 127.0.0.1/localhost 指向手机本身, 请改用电脑的局域网 IPv4 地址。');
  }
  return '${error.toString()}\n\n${hints.join('\n')}';
}
