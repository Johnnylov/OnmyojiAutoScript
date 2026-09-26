import 'dart:convert';

import 'package:flutter_nb_net/flutter_net.dart';
import 'package:oasx/api/api_client.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

typedef JsonObject = Map<String, dynamic>;

JsonObject object(Object? value) => value is Map
    ? value.map((key, value) => MapEntry(key.toString(), value))
    : <String, dynamic>{};

List<JsonObject> objects(Object? value) => value is List
    ? value.whereType<Map>().map((value) => object(value)).toList()
    : <JsonObject>[];

String textValue(Object? value, [String fallback = '—']) =>
    value == null || value.toString().isEmpty ? fallback : value.toString();

num? numberValue(Object? value) =>
    value is num ? value : num.tryParse(value?.toString() ?? '');

class SolanaApiException implements Exception {
  final String code;
  final String message;
  final int? status;
  const SolanaApiException(this.code, this.message, [this.status]);

  bool get unavailable => status == null || status == 502 || status == 503;

  @override
  String toString() => message;
}

/// A separate, uncached transport: stale HTTP cache entries must never become
/// apparent live state or successful control responses.
class SolanaApi {
  final Dio _http;
  final String Function() address;

  SolanaApi({Dio? transport, String Function()? address})
    : _http =
          transport ??
          Dio(
            BaseOptions(
              connectTimeout: const Duration(seconds: 5),
              receiveTimeout: const Duration(seconds: 12),
            ),
          ),
      address = address ?? (() => ApiClient().address);

  Uri get baseUri {
    final value = address();
    return Uri.parse(value.contains('://') ? value : 'http://$value');
  }

  Future<JsonObject> get(String path, {JsonObject? query}) =>
      request('GET', path, query: query);

  Future<JsonObject> request(
    String method,
    String path, {
    JsonObject? body,
    JsonObject? query,
  }) async {
    try {
      final response = await _http.request<dynamic>(
        baseUri.resolve(path).toString(),
        data: body,
        queryParameters: query,
        options: Options(
          method: method,
          headers: {'Cache-Control': 'no-store'},
        ),
      );
      final decoded = response.data is String
          ? jsonDecode(response.data as String)
          : response.data;
      if (decoded is! Map) {
        throw const SolanaApiException('invalid_response', '服务返回了无法识别的数据');
      }
      final result = object(decoded);
      if (result['error'] != null) {
        final error = object(result['error']);
        throw SolanaApiException(
          textValue(error['code'], 'request_failed'),
          textValue(error['message'], result['error'].toString()),
        );
      }
      return result['data'] is Map ? object(result['data']) : result;
    } on SolanaApiException {
      rethrow;
    } on DioException catch (error) {
      final detail = object(error.response?.data);
      final nested = object(detail['detail']);
      final apiError = object(detail['error']);
      final payload = apiError.isNotEmpty ? apiError : nested;
      final code = textValue(
        payload['code'],
        error.response?.statusCode == 404 ? 'unsupported' : 'connection_error',
      );
      throw SolanaApiException(
        code,
        textValue(
          payload['message'],
          error.response == null
              ? '无法连接后端，请检查服务和连接地址'
              : '请求失败（${error.response?.statusCode}）',
        ),
        error.response?.statusCode,
      );
    } on FormatException {
      throw const SolanaApiException('invalid_response', '服务响应格式不正确');
    }
  }

  WebSocketChannel connect({String? streamId, int? after}) {
    final base = baseUri;
    final uri = base
        .resolve('/api/v2/events')
        .replace(
          scheme: base.scheme == 'https' ? 'wss' : 'ws',
          queryParameters: {
            if (streamId != null) 'stream_id': streamId,
            if (after != null) 'after': '$after',
          },
        );
    return WebSocketChannel.connect(uri);
  }

  void dispose() => _http.close();
}
