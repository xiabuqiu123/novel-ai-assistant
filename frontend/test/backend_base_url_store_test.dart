import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:frontend/app_utils.dart';
import 'package:frontend/backend_base_url_store.dart';

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  test('未保存时返回平台默认后端地址', () async {
    final loaded = await BackendBaseUrlStore.load();
    expect(loaded, defaultBackendBaseUrl);
  });

  test('保存后能持久化读取，且自动补全协议并去除尾斜杠', () async {
    final saved = await BackendBaseUrlStore.save(' 192.168.1.162:8000/');
    expect(saved, 'http://192.168.1.162:8000');

    // 模拟应用重启：重新实例化偏好读取
    final reloaded = await BackendBaseUrlStore.load();
    expect(reloaded, 'http://192.168.1.162:8000');
  });

  test('清除后回退到平台默认后端地址', () async {
    await BackendBaseUrlStore.save('http://10.0.2.2:8000');
    await BackendBaseUrlStore.clear();
    final loaded = await BackendBaseUrlStore.load();
    expect(loaded, defaultBackendBaseUrl);
  });

  test('空字符串保存后回退到平台默认后端地址', () async {
    await BackendBaseUrlStore.save('http://10.0.2.2:8000');
    final saved = await BackendBaseUrlStore.save('   ');
    expect(saved, defaultBackendBaseUrl);

    final loaded = await BackendBaseUrlStore.load();
    expect(loaded, defaultBackendBaseUrl);
  });
}