// ignore_for_file: avoid_web_libraries_in_flutter, deprecated_member_use

import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:typed_data';

import 'api_client.dart';

Future<Map<String, dynamic>> sendJsonRequest(
  String method,
  Uri uri, {
  List<int>? body,
  Map<String, String>? headers,
}) async {
  final request = html.HttpRequest();
  request.open(method, uri.toString(), async: true);
  request.setRequestHeader('accept', 'application/json');
  for (final entry in (headers ?? const <String, String>{}).entries) {
    request.setRequestHeader(entry.key, entry.value);
  }

  final completer = Completer<html.HttpRequest>();
  request.onLoad.listen((_) => completer.complete(request));
  request.onError.listen((_) {
    if (!completer.isCompleted) {
      completer.completeError(ApiException(0, 'Cannot reach backend'));
    }
  });
  request.onTimeout.listen((_) {
    if (!completer.isCompleted) {
      completer.completeError(ApiException(0, 'Backend request timed out'));
    }
  });
  request.timeout = 20000;

  if (body == null) {
    request.send();
  } else {
    request.send(Uint8List.fromList(body));
  }

  final response = await completer.future;
  final responseBody = response.responseText ?? '';
  final decoded = responseBody.isEmpty ? <String, dynamic>{} : jsonDecode(responseBody);
  final status = response.status ?? 0;
  if (status < 200 || status >= 300) {
    throw ApiException(status, responseBody);
  }
  if (decoded is Map<String, dynamic>) {
    return decoded;
  }
  return {'raw': decoded};
}
