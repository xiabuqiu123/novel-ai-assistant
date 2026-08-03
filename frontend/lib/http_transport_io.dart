import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'api_client.dart';

Future<Map<String, dynamic>> sendJsonRequest(
  String method,
  Uri uri, {
  List<int>? body,
  Map<String, String>? headers,
}) async {
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 8);
  try {
    final request = await client.openUrl(method, uri);
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    for (final entry in (headers ?? const <String, String>{}).entries) {
      request.headers.set(entry.key, entry.value);
    }
    if (body != null) {
      request.contentLength = body.length;
      request.add(body);
    }
    final response = await request.close().timeout(const Duration(seconds: 20));
    final responseBody = await utf8.decodeStream(response);
    final decoded = responseBody.isEmpty ? <String, dynamic>{} : jsonDecode(responseBody);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(response.statusCode, responseBody);
    }
    if (decoded is Map<String, dynamic>) {
      return decoded;
    }
    return {'raw': decoded};
  } on SocketException catch (error) {
    throw ApiException(0, 'Cannot reach backend: ${error.message}');
  } on TimeoutException {
    throw ApiException(0, 'Backend request timed out');
  } finally {
    client.close(force: true);
  }
}
