import 'package:shared_preferences/shared_preferences.dart';

import 'app_utils.dart';

/// 本地持久化的后端地址存储。
///
/// 背景：真机连接桌面后端时，127.0.0.1/10.0.2.2 等默认地址都无法路由到 PC，
/// 用户必须手动改成电脑的局域网 IPv4。此前 [_backendBaseUrl] 仅存于内存，
/// APK 重启即丢失，导致每次冷启动都重新触发“远程计算机拒绝网络连接”。
/// 本类把用户保存过的地址持久化下来，从根上消除反复配置的问题。
class BackendBaseUrlStore {
  BackendBaseUrlStore._();

  static const String _key = 'backend_base_url';

  /// 读取已保存的后端地址；未保存或为空则返回平台默认值。
  static Future<String> load() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(_key);
    if (saved == null || saved.trim().isEmpty) {
      return defaultBackendBaseUrl;
    }
    return normalizeBackendBaseUrl(saved);
  }

  /// 规范化并持久化后端地址。
  static Future<String> save(String value) async {
    final normalized = normalizeBackendBaseUrl(value);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key, normalized);
    return normalized;
  }

  /// 清除保存的地址，恢复为平台默认值（主要供测试使用）。
  static Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_key);
  }
}