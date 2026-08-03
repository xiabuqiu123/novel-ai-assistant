import 'dart:convert';

import 'http_transport.dart';

class NovelApiClient {
  NovelApiClient({required this.baseUrl});

  final String baseUrl;

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Future<Map<String, dynamic>> health() async {
    return _jsonRequest('GET', _uri('/health'));
  }

  Future<List<Novel>> listNovels() async {
    final data = await _jsonRequest('GET', _uri('/novels'));
    final items = data['items'];
    if (items is List) {
      return items.map((item) => Novel.fromJson(item)).toList();
    }
    if (data['raw'] is List) {
      return (data['raw'] as List).map((item) => Novel.fromJson(item)).toList();
    }
    return <Novel>[];
  }

  Future<List<ChapterSummary>> listChapters(int novelId) async {
    final data = await _jsonRequest('GET', _uri('/novels/$novelId/chapters')); 
    final raw = data['raw'];
    if (raw is! List) {
      return <ChapterSummary>[];
    }
    return raw.map((item) => ChapterSummary.fromJson(item)).toList();
  }

  Future<Chapter> getChapter(int chapterId) async {
    final data = await _jsonRequest('GET', _uri('/chapters/$chapterId')); 
    return Chapter.fromJson(data);
  }

  Future<ChapterSummaryResult> summarizeChapter(int chapterId, {bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/chapters/$chapterId/summary'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return ChapterSummaryResult.fromJson(data);
  }
  Future<JobStartResult> startChapterSummary(int chapterId, {bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/chapters/$chapterId/summary/start'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return JobStartResult.fromJson(data);
  }

    Future<JobStartResult> startOutline(int novelId, {bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/outline/start'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return JobStartResult.fromJson(data);
  }

  Future<JobResultResponse> getJobResult(int jobId) async {
    final data = await _jsonRequest('GET', _uri('/analysis-jobs/$jobId/result'));
    return JobResultResponse.fromJson(data);
  }

  Future<JobStartResult> startQa({
    required int novelId,
    required String question,
    String? model,
    bool forceRefresh = false,
  }) async {
    final body = <String, dynamic>{
      'question': question,
      'force_refresh': forceRefresh,
      if (model != null && model.trim().isNotEmpty) 'model': model.trim(),
    };
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/qa/start'),
      body: utf8.encode(jsonEncode(body)),
      headers: {'content-type': 'application/json'},
    );
    return JobStartResult.fromJson(data);
  }

  Future<BookOutlineResult> generateOutline(int novelId, {bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/outline'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return BookOutlineResult.fromJson(data);
  }

  Future<JobStartResult> startStageOutline(int novelId, {bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/stage-outline/start'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return JobStartResult.fromJson(data);
  }

  Future<BookStageOutlineResult> generateStageOutline(int novelId, {bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/stage-outline'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return BookStageOutlineResult.fromJson(data);
  }

  Future<QaResult> askQuestion({
    required int novelId,
    required String question,
    String? model,
    bool forceRefresh = false,
  }) async {
    final body = <String, dynamic>{
      'question': question,
      'force_refresh': forceRefresh,
      if (model != null && model.trim().isNotEmpty) 'model': model.trim(),
    };
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/qa'),
      body: utf8.encode(jsonEncode(body)),
      headers: {'content-type': 'application/json'},
    );
    return QaResult.fromJson(data);
  }

    Future<JobStartResult> startCharacters({required int novelId, bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/characters/start'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return JobStartResult.fromJson(data);
  }

  Future<CharacterListResult> extractCharacters({
    required int novelId,
    String? model,
    bool forceRefresh = false,
  }) async {
    final body = <String, dynamic>{
      'force_refresh': forceRefresh,
      if (model != null && model.trim().isNotEmpty) 'model': model.trim(),
    };
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/characters'),
      body: utf8.encode(jsonEncode(body)),
      headers: {'content-type': 'application/json'},
    );
    return CharacterListResult.fromJson(data);
  }

  Future<JobStartResult> startRelationships({required int novelId, bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/relationships/start'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return JobStartResult.fromJson(data);
  }

  Future<RelationshipGraphResult> fetchRelationshipGraph({required int novelId}) async {
    final data = await _jsonRequest('GET', _uri('/novels/$novelId/relationships/graph'));
    return RelationshipGraphResult.fromJson(data);
  }
  Future<List<ExtractedFact>> listFacts({
    required int novelId,
    String? factType,
    String? status,
  }) async {
    final params = <String>[
      if (factType != null) 'fact_type=${Uri.encodeQueryComponent(factType)}',
      if (status != null) 'status=${Uri.encodeQueryComponent(status)}',
    ];
    final query = params.isEmpty ? '' : '?${params.join('&')}';
    final data = await _jsonRequest('GET', _uri('/novels/$novelId/facts$query'));
    final raw = data['raw'];
    if (raw is! List) {
      return <ExtractedFact>[];
    }
    return raw.map((item) => ExtractedFact.fromJson(item)).toList();
  }

  Future<ReviewUpdateResult> updateReviewStatus({
    required String recordType,
    required int recordId,
    required String status,
    String note = '',
  }) async {
    final data = await _jsonRequest(
      'PATCH',
      _uri('/review/$recordType/$recordId'),
      body: utf8.encode(jsonEncode({'status': status, 'note': note})),
      headers: {'content-type': 'application/json'},
    );
    return ReviewUpdateResult.fromJson(data);
  }
  Future<ImportTxtResult> importTxt({
    required String title,
    required String text,
  }) async {
    final boundary = 'novel-mvp-${DateTime.now().microsecondsSinceEpoch}';
    final body = _multipartBody(
      boundary: boundary,
      title: title,
      filename: '${_safeFilename(title)}.txt',
      fileBytes: utf8.encode(text),
      contentType: 'text/plain; charset=utf-8',
    );
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/import-txt'),
      body: body,
      headers: {'content-type': 'multipart/form-data; boundary=$boundary'},
    );
    return ImportTxtResult.fromJson(data);
  }

  Future<ImportTxtResult> importTxtFile({
    required String title,
    required String filename,
    required List<int> bytes,
  }) async {
    final boundary = 'novel-mvp-${DateTime.now().microsecondsSinceEpoch}';
    final body = _multipartBody(
      boundary: boundary,
      title: title,
      filename: filename,
      fileBytes: bytes,
    );
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/import-txt'),
      body: body,
      headers: {'content-type': 'multipart/form-data; boundary=$boundary'},
    );
    return ImportTxtResult.fromJson(data);
  }
  Future<List<AnalysisJob>> listAnalysisJobs({int? novelId}) async {
    final path = novelId != null ? '/analysis-jobs?novel_id=$novelId' : '/analysis-jobs';
    final data = await _jsonRequest('GET', _uri(path));
    final raw = data['raw'];
    if (raw is! List) {
      return <AnalysisJob>[];
    }
    return raw.map((item) => AnalysisJob.fromJson(item)).toList();
  }

  Future<AnalysisJob> getAnalysisJob(int jobId) async {
    final data = await _jsonRequest('GET', _uri('/analysis-jobs/$jobId'));
    return AnalysisJob.fromJson(data);
  }

  Future<AnalysisJob> retryAnalysisJob(int jobId) async {
    final data = await _jsonRequest('POST', _uri('/analysis-jobs/$jobId/retry'));
    return AnalysisJob.fromJson(data);
  }

  Future<Map<String, dynamic>> runAnalysisJob(int jobId) async {
    return _jsonRequest('POST', _uri('/analysis-jobs/$jobId/run'));
  }

  Future<Map<String, dynamic>> runNextAnalysisJob() async {
    return _jsonRequest('POST', _uri('/analysis-jobs/run-next'));
  }

  Future<JobStartResult> startWholeBookAnalysis(int novelId, {bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/analyze-all/start'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return JobStartResult.fromJson(data);
  }

  Future<AnalysisJob> cancelAnalysisJob(int jobId) async {
    final data = await _jsonRequest('POST', _uri('/analysis-jobs/$jobId/cancel'));
    return AnalysisJob.fromJson(data);
  }

  Future<JobStartResult> startSettings({required int novelId, bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/settings/start'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return JobStartResult.fromJson(data);
  }

  Future<JobStartResult> startEvents({required int novelId, bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/events/start'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return JobStartResult.fromJson(data);
  }

  Future<JobStartResult> startConflicts({required int novelId, bool forceRefresh = false}) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/novels/$novelId/conflicts/start'),
      body: utf8.encode(jsonEncode({'force_refresh': forceRefresh})),
      headers: {'content-type': 'application/json'},
    );
    return JobStartResult.fromJson(data);
  }

  Future<ModelSettings> getModelSettings() async {
    final data = await _jsonRequest('GET', _uri('/settings/model'));
    return ModelSettings.fromJson(data);
  }

  Future<UsageStats> fetchUsageStats() async {
    final data = await _jsonRequest('GET', _uri('/usage-stats'));
    return UsageStats.fromJson(data);
  }

  Future<void> saveModelSettings({
    required String apiKey,
    required String baseUrl,
    required String model,
  }) async {
    await _jsonRequest(
      'POST',
      _uri('/settings/model'),
      body: utf8.encode(
        jsonEncode({'api_key': apiKey, 'base_url': baseUrl, 'model': model}),
      ),
      headers: {'content-type': 'application/json'},
    );
  }

  Future<DeleteNovelResult> deleteNovel(int novelId) async {
    final data = await _jsonRequest('DELETE', _uri('/novels/$novelId'));
    return DeleteNovelResult.fromJson(data);
  }

  Future<ClearCacheResult> clearNovelCache(int novelId, {String? taskType}) async {
    final query = taskType == null ? '' : '?task_type=${Uri.encodeQueryComponent(taskType)}';
    final data = await _jsonRequest('DELETE', _uri('/novels/$novelId/cache$query'));
    return ClearCacheResult.fromJson(data);
  }

  Future<Map<String, dynamic>> exportMarkdown(int novelId) async {
    return _jsonRequest('GET', _uri('/novels/$novelId/export/markdown'));
  }

  Future<Map<String, dynamic>> exportReport(int novelId, {bool includeChapters = true}) async {
    final query = includeChapters ? '' : '?include_chapters=false';
    return _jsonRequest('GET', _uri('/novels/$novelId/export/report$query'));
  }

  Future<ModelConnectionTestResult> testConnection({
    required String apiKey,
    required String baseUrl,
    required String model,
  }) async {
    final data = await _jsonRequest(
      'POST',
      _uri('/settings/model/test'),
      body: utf8.encode(jsonEncode({'api_key': apiKey, 'base_url': baseUrl, 'model': model})),
      headers: {'content-type': 'application/json'},
    );
    return ModelConnectionTestResult.fromJson(data);
  }

  Future<Map<String, dynamic>> _jsonRequest(
    String method,
    Uri uri, {
    List<int>? body,
    Map<String, String>? headers,
  }) async {
    return sendJsonRequest(method, uri, body: body, headers: headers);
  }
}

List<int> _multipartBody({
  required String boundary,
  required String title,
  required String filename,
  required List<int> fileBytes,
  String contentType = 'application/octet-stream',
}) {
  final chunks = <int>[];

  void addAscii(String value) => chunks.addAll(ascii.encode(value));
  void addUtf8(String value) => chunks.addAll(utf8.encode(value));

  addAscii('--$boundary\r\n');
  addAscii('Content-Disposition: form-data; name="title"\r\n\r\n');
  addUtf8(title);
  addAscii('\r\n');

  addAscii('--$boundary\r\n');
  addAscii('Content-Disposition: form-data; name="chunk_size"\r\n\r\n');
  addAscii('6000\r\n');

  addAscii('--$boundary\r\n');
  addAscii('Content-Disposition: form-data; name="file"; filename="');
  addUtf8(filename);
  addAscii('"\r\n');
  addAscii('Content-Type: $contentType\r\n\r\n');
  chunks.addAll(fileBytes);
  addAscii('\r\n--$boundary--\r\n');

  return chunks;
}
String _safeFilename(String value) {
  final cleaned = value.trim().replaceAll(RegExp(r'[^A-Za-z0-9_-]+'), '-');
  return cleaned.isEmpty ? 'novel' : cleaned;
}

class ImportTxtResult {
  ImportTxtResult({
    required this.id,
    required this.title,
    required this.imported,
    required this.chapterCount,
    required this.chunkCount,
    required this.encoding,
    this.duplicateOf,
    this.requestedTitle = '',
    this.requestedSourceFilename = '',
  });

  final int id;
  final String title;
  final bool imported;
  final int chapterCount;
  final int chunkCount;
  final String encoding;
  final Novel? duplicateOf;
  final String requestedTitle;
  final String requestedSourceFilename;

  factory ImportTxtResult.fromJson(Map<String, dynamic> json) {
    return ImportTxtResult(
      id: (json['id'] as num).toInt(),
      title: json['title'] as String? ?? 'Untitled',
      imported: json['imported'] == true,
      chapterCount: (json['chapter_count'] as num? ?? 0).toInt(),
      chunkCount: (json['chunk_count'] as num? ?? 0).toInt(),
      encoding: json['encoding'] as String? ?? '',
      duplicateOf: json['duplicate_of'] is Map<String, dynamic>
          ? Novel.fromJson(json['duplicate_of'])
          : null,
      requestedTitle: json['requested_title'] as String? ?? '',
      requestedSourceFilename: json['requested_source_filename'] as String? ?? '',
    );
  }
}

class ClearCacheResult {
  ClearCacheResult({
    required this.cleared,
    required this.novelId,
    required this.title,
    required this.taskType,
    required this.deletedCacheEntries,
  });

  final bool cleared;
  final int novelId;
  final String title;
  final String taskType;
  final int deletedCacheEntries;

  factory ClearCacheResult.fromJson(Map<String, dynamic> json) {
    return ClearCacheResult(
      cleared: json['cleared'] == true,
      novelId: (json['novel_id'] as num? ?? 0).toInt(),
      title: json['title'] as String? ?? '',
      taskType: json['task_type'] as String? ?? 'all',
      deletedCacheEntries: (json['deleted_cache_entries'] as num? ?? 0).toInt(),
    );
  }
}

class DeleteNovelResult {
  DeleteNovelResult({
    required this.deleted,
    required this.novelId,
    required this.title,
    required this.deletedCacheEntries,
  });

  final bool deleted;
  final int novelId;
  final String title;
  final int deletedCacheEntries;

  factory DeleteNovelResult.fromJson(Map<String, dynamic> json) {
    return DeleteNovelResult(
      deleted: json['deleted'] == true,
      novelId: (json['novel_id'] as num? ?? 0).toInt(),
      title: json['title'] as String? ?? '',
      deletedCacheEntries: (json['deleted_cache_entries'] as num? ?? 0).toInt(),
    );
  }
}

class ModelConnectionTestResult {
  ModelConnectionTestResult({
    required this.ok,
    required this.status,
    required this.message,
    required this.model,
    required this.baseUrl,
    this.httpStatus,
  });

  final bool ok;
  final String status;
  final String message;
  final String model;
  final String baseUrl;
  final int? httpStatus;

  factory ModelConnectionTestResult.fromJson(Map<String, dynamic> json) {
    return ModelConnectionTestResult(
      ok: json['ok'] == true,
      status: json['status'] as String? ?? 'unknown',
      message: json['message'] as String? ?? '',
      model: json['model'] as String? ?? '',
      baseUrl: json['base_url'] as String? ?? '',
      httpStatus: (json['http_status'] as num?)?.toInt(),
    );
  }

  String get displayMessage {
    final statusPart = httpStatus == null ? status : '$status HTTP $httpStatus';
    final details = message.isEmpty ? statusPart : '$statusPart: $message';
    return '$details ($model @ $baseUrl)';
  }
}

class ApiException implements Exception {
  ApiException(this.statusCode, this.message);

  final int statusCode;
  final String message;

  @override
  String toString() => statusCode == 0 ? message : 'HTTP $statusCode: $message';
}

class Novel {
  Novel({
    required this.id,
    required this.title,
    required this.chapterCount,
    required this.chunkCount,
    required this.encoding,
  });

  final int id;
  final String title;
  final int chapterCount;
  final int chunkCount;
  final String encoding;

  factory Novel.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    return Novel(
      id: (map['id'] as num).toInt(),
      title: map['title'] as String? ?? 'Untitled',
      chapterCount: (map['chapter_count'] as num? ?? 0).toInt(),
      chunkCount: (map['chunk_count'] as num? ?? 0).toInt(),
      encoding: map['encoding'] as String? ?? '',
    );
  }
}

class ChapterSummary {
  ChapterSummary({
    required this.id,
    required this.order,
    required this.title,
    required this.charCount,
  });

  final int id;
  final int order;
  final String title;
  final int charCount;

  factory ChapterSummary.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    return ChapterSummary(
      id: (map['id'] as num).toInt(),
      order: (map['chapter_order'] as num? ?? 0).toInt(),
      title: map['title'] as String? ?? 'Untitled',
      charCount: (map['char_count'] as num? ?? 0).toInt(),
    );
  }
}

class Chapter {
  Chapter({
    required this.id,
    required this.novelId,
    required this.order,
    required this.title,
    required this.content,
  });

  final int id;
  final int novelId;
  final int order;
  final String title;
  final String content;

  factory Chapter.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    return Chapter(
      id: (map['id'] as num).toInt(),
      novelId: (map['novel_id'] as num? ?? 0).toInt(),
      order: (map['chapter_order'] as num? ?? 0).toInt(),
      title: map['title'] as String? ?? 'Untitled',
      content: map['content'] as String? ?? '',
    );
  }
}

class ModelProvenance {
  ModelProvenance({
    required this.taskType,
    required this.modelUsed,
    required this.source,
    required this.cacheHit,
    required this.localFallback,
    required this.modelError,
    required this.cacheKey,
    required this.providerCallAttempted,
    required this.providerCallSucceeded,
    this.jobId,
  });

  final String taskType;
  final String modelUsed;
  final String source;
  final bool cacheHit;
  final bool localFallback;
  final String modelError;
  final String cacheKey;
  final bool providerCallAttempted;
  final bool providerCallSucceeded;
  final int? jobId;

  factory ModelProvenance.fromResponse(Map<String, dynamic> json) {
    final raw = json['provenance'];
    final map = raw is Map<String, dynamic> ? raw : json;
    return ModelProvenance(
      taskType: map['task_type'] as String? ?? '',
      modelUsed: map['model_used'] as String? ?? '',
      source: map['source'] as String? ?? '',
      cacheHit: map['cache_hit'] == true,
      localFallback: map['local_fallback'] == true,
      modelError: map['model_error'] as String? ?? '',
      cacheKey: map['cache_key'] as String? ?? '',
      providerCallAttempted: map['provider_call_attempted'] == true,
      providerCallSucceeded: map['provider_call_succeeded'] == true,
      jobId: (map['job_id'] as num?)?.toInt(),
    );
  }
}

class ChapterSummaryResult {
  ChapterSummaryResult({
    required this.status,
    required this.shortSummary,
    required this.keyEvents,
    required this.cacheHit,
    required this.jobId,
    required this.provenance,
  });

  final String status;
  final String shortSummary;
  final List<String> keyEvents;
  final bool cacheHit;
  final int? jobId;
  final ModelProvenance provenance;

  factory ChapterSummaryResult.fromJson(Map<String, dynamic> json) {
    final rawEvents = json['key_events'];
    return ChapterSummaryResult(
      status: json['status'] as String? ?? 'unknown',
      shortSummary: json['short_summary'] as String? ?? '',
      keyEvents: rawEvents is List
          ? rawEvents.map((item) => item.toString()).where((item) => item.isNotEmpty).toList()
          : <String>[],
      cacheHit: json['cache_hit'] == true,
      jobId: (json['job_id'] as num?)?.toInt(),
      provenance: ModelProvenance.fromResponse(json),
    );
  }
}

class BookOutlineChapter {
  BookOutlineChapter({
    required this.order,
    required this.title,
    required this.brief,
    required this.isValid,
  });

  final int order;
  final String title;
  final String brief;
  final bool isValid;

  factory BookOutlineChapter.fromJson(Object? json) {
    final map = json is Map<String, dynamic> ? json : <String, dynamic>{};
    final title = _firstString(map, const ['chapter_title', 'title', 'name']);
    final brief = _firstString(map, const ['brief', 'summary', 'description', 'content']);
    final order = _firstInt(map, const ['chapter_order', 'order', 'chapter', 'chapter_number']);
    return BookOutlineChapter(
      order: order,
      title: title.isEmpty ? 'Untitled' : title,
      brief: brief,
      isValid: order > 0 || title.isNotEmpty || brief.isNotEmpty,
    );
  }
}

class BookOutlineResult {
  BookOutlineResult({
    required this.status,
    required this.title,
    required this.chapters,
    required this.cacheHit,
    required this.jobId,
    required this.provenance,
    this.modelError = "",
    this.parseError = "",
  });

  final String status;
  final String title;
  final List<BookOutlineChapter> chapters;
  final bool cacheHit;
  final int? jobId;
  final ModelProvenance provenance;
  final String modelError;
  final String parseError;
  bool get hasParseError => parseError.isNotEmpty;

  factory BookOutlineResult.fromJson(Map<String, dynamic> json) {
    final outline = json['outline'] is Map<String, dynamic>
        ? json['outline'] as Map<String, dynamic>
        : <String, dynamic>{};
    final rawChapters = outline['chapters'] ?? json['chapters'];
    final parsedChapters = rawChapters is List
        ? rawChapters.map((item) => BookOutlineChapter.fromJson(item)).where((item) => item.isValid).toList()
        : <BookOutlineChapter>[];
    return BookOutlineResult(
      status: json['status'] as String? ?? 'unknown',
      title: (json['title'] as String?) ?? (outline['title'] as String?) ?? 'Book outline',
      chapters: parsedChapters,
      cacheHit: json['cache_hit'] == true,
      jobId: (json['job_id'] as num?)?.toInt(),
      provenance: ModelProvenance.fromResponse(json),
      modelError: json['model_error'] as String? ?? '',
      parseError: _outlineParseError(rawChapters, parsedChapters, json),
    );
  }
}

String _firstString(Map<String, dynamic> map, List<String> keys) {
  for (final key in keys) {
    final value = map[key];
    if (value == null) continue;
    final text = value.toString().trim();
    if (text.isNotEmpty) return text;
  }
  return '';
}

int _firstInt(Map<String, dynamic> map, List<String> keys) {
  for (final key in keys) {
    final value = map[key];
    if (value is num) return value.toInt();
    if (value is String) {
      final match = RegExp(r'\d+').firstMatch(value);
      if (match != null) return int.parse(match.group(0)!);
    }
  }
  return 0;
}

String _outlineParseError(Object? rawChapters, List<BookOutlineChapter> chapters, Map<String, dynamic> json) {
  final backendError = json['model_error'] as String? ?? '';
  if ((json['status'] as String?) == 'parse_error') {
    return backendError.isNotEmpty ? backendError : 'Model response could not be parsed as a usable outline.';
  }
  if (rawChapters is! List) {
    return 'Model response did not include outline.chapters.';
  }
  if (rawChapters.isNotEmpty && chapters.isEmpty) {
    return 'Outline chapters were present but none had order, title, or summary fields.';
  }
  final allDefault = chapters.isNotEmpty && chapters.every((chapter) => chapter.order == 0 && chapter.title == 'Untitled' && chapter.brief.isEmpty);
  if (allDefault) {
    return 'Outline parsed only placeholder chapter values.';
  }
  final allBriefsEmpty = chapters.isNotEmpty && chapters.every((chapter) => chapter.brief.trim().isEmpty);
  if (allBriefsEmpty) {
    return 'Outline contains chapter titles but no chapter summaries.';
  }
  return '';
}

class BookStageOutlineStage {
  BookStageOutlineStage({
    required this.stageIndex,
    required this.title,
    required this.chapterStart,
    required this.chapterEnd,
    required this.location,
    required this.characters,
    required this.event,
    required this.resolution,
    required this.outcome,
    required this.isValid,
  });

  final int stageIndex;
  final String title;
  final int chapterStart;
  final int chapterEnd;
  final String location;
  final List<String> characters;
  final String event;
  final String resolution;
  final String outcome;
  final bool isValid;

  String get chapterRange => '第 $chapterStart-$chapterEnd 章';

  factory BookStageOutlineStage.fromJson(Object? json) {
    final map = json is Map<String, dynamic> ? json : <String, dynamic>{};
    final rawChars = map['characters'];
    final characters = rawChars is List
        ? rawChars.map((item) => item.toString().trim()).where((item) => item.isNotEmpty).toList()
        : <String>[];
    final title = _firstString(map, const ['title', 'stage_title']);
    final event = _firstString(map, const ['event']);
    final resolution = _firstString(map, const ['resolution']);
    final outcome = _firstString(map, const ['outcome']);
    final cs = _firstInt(map, const ['chapter_start']);
    final ce = _firstInt(map, const ['chapter_end']);
    final stageIndex = _firstInt(map, const ['stage_index', 'index']);
    return BookStageOutlineStage(
      stageIndex: stageIndex,
      title: title,
      chapterStart: cs,
      chapterEnd: ce,
      location: _firstString(map, const ['location']),
      characters: characters,
      event: event,
      resolution: resolution,
      outcome: outcome,
      isValid: stageIndex > 0 || title.isNotEmpty || event.isNotEmpty || resolution.isNotEmpty || outcome.isNotEmpty,
    );
  }
}

class BookStageOutlineResult {
  BookStageOutlineResult({
    required this.status,
    required this.stages,
    required this.evidence,
    required this.cacheHit,
    required this.jobId,
    required this.provenance,
    this.modelError = '',
    this.parseError = '',
  });

  final String status;
  final List<BookStageOutlineStage> stages;
  final List<Map<String, dynamic>> evidence;
  final bool cacheHit;
  final int? jobId;
  final ModelProvenance provenance;
  final String modelError;
  final String parseError;
  bool get hasParseError => parseError.isNotEmpty;

  factory BookStageOutlineResult.fromJson(Map<String, dynamic> json) {
    final rawStages = json['stages'];
    final parsedStages = rawStages is List
        ? rawStages.map((item) => BookStageOutlineStage.fromJson(item)).where((item) => item.isValid).toList()
        : <BookStageOutlineStage>[];
    final rawEvidence = json['evidence'];
    final evidence = rawEvidence is List
        ? rawEvidence.whereType<Map<String, dynamic>>().toList()
        : <Map<String, dynamic>>[];
    return BookStageOutlineResult(
      status: json['status'] as String? ?? 'unknown',
      stages: parsedStages,
      evidence: evidence,
      cacheHit: json['cache_hit'] == true,
      jobId: (json['job_id'] as num?)?.toInt(),
      provenance: ModelProvenance.fromResponse(json),
      modelError: json['model_error'] as String? ?? '',
      parseError: _stageOutlineParseError(rawStages, parsedStages, json),
    );
  }
}

String _stageOutlineParseError(
  Object? rawStages,
  List<BookStageOutlineStage> stages,
  Map<String, dynamic> json,
) {
  final backendError = json['model_error'] as String? ?? '';
  if ((json['status'] as String?) == 'parse_error') {
    return backendError.isNotEmpty ? backendError : 'Model response could not be parsed as a usable stages list.';
  }
  if (rawStages is! List) {
    return 'Model response did not include a stages list.';
  }
  if (rawStages.isNotEmpty && stages.isEmpty) {
    return 'Stage entries were present but none had usable fields.';
  }
  return '';
}

class UsageStats {
  UsageStats({
    required this.cacheEntries,
    required this.modelCallsAttempted,
    required this.modelCallsSucceeded,
    required this.localFallbackResults,
    required this.failedJobs,
    required this.tokenStatsAvailable,
  });

  final int cacheEntries;
  final int modelCallsAttempted;
  final int modelCallsSucceeded;
  final int localFallbackResults;
  final int failedJobs;
  final bool tokenStatsAvailable;

  factory UsageStats.fromJson(Map<String, dynamic> json) {
    return UsageStats(
      cacheEntries: (json['cache_entries'] as num?)?.toInt() ?? 0,
      modelCallsAttempted: (json['model_calls_attempted'] as num?)?.toInt() ?? 0,
      modelCallsSucceeded: (json['model_calls_succeeded'] as num?)?.toInt() ?? 0,
      localFallbackResults: (json['local_fallback_results'] as num?)?.toInt() ?? 0,
      failedJobs: (json['failed_jobs'] as num?)?.toInt() ?? 0,
      tokenStatsAvailable: json['token_stats_available'] == true,
    );
  }
}

class ModelSettings {
  ModelSettings({
    required this.apiKeySet,
    required this.baseUrl,
    required this.model,
  });

  final bool apiKeySet;
  final String baseUrl;
  final String model;

  factory ModelSettings.fromJson(Map<String, dynamic> json) {
    return ModelSettings(
      apiKeySet: json['api_key_set'] == 'yes',
      baseUrl: json['base_url'] as String? ?? '',
      model: json['model'] as String? ?? 'gpt-4.1-mini',
    );
  }
}


class QaResult {
  QaResult({
    required this.status,
    required this.answer,
    required this.evidence,
    required this.reasoning,
    required this.uncertainty,
    required this.needsMoreContext,
    required this.cacheHit,
    required this.provenance,
    this.jobId,
  });

  final String status;
  final String answer;
  final List<QaEvidence> evidence;
  final String reasoning;
  final String uncertainty;
  final bool needsMoreContext;
  final bool cacheHit;
  final ModelProvenance provenance;
  final int? jobId;

  factory QaResult.fromJson(Map<String, dynamic> json) {
    final normalized = _mergedQaPayload(json);
    final rawEvidence = normalized['evidence'];
    final List<QaEvidence> evidenceList;
    if (rawEvidence is List) {
      evidenceList = rawEvidence.map((e) => QaEvidence.fromJson(e)).toList();
    } else {
      evidenceList = <QaEvidence>[];
    }
    final answer = _qaAnswerText(normalized);
    return QaResult(
      status: normalized['status'] as String? ?? '',
      answer: answer.isNotEmpty ? answer : 'Model returned no usable answer fields.',
      evidence: evidenceList,
      provenance: ModelProvenance.fromResponse(json),
      reasoning: _qaScalarText(normalized['reasoning']),
      uncertainty: _qaScalarText(normalized['uncertainty']),
      needsMoreContext: normalized['needs_more_context'] == true,
      cacheHit: normalized['cache_hit'] == true,
      jobId: (normalized['job_id'] as num?)?.toInt(),
    );
  }
}

Map<String, dynamic> _mergedQaPayload(Map<String, dynamic> json) {
  final merged = Map<String, dynamic>.from(json);
  for (final key in const ['result', 'parsed_json']) {
    final nested = merged[key];
    if (nested is Map<String, dynamic>) {
      for (final entry in nested.entries) {
        merged.putIfAbsent(entry.key, () => entry.value);
      }
    }
  }
  return merged;
}

String _qaAnswerText(Map<String, dynamic> json) {
  for (final key in const ['answer', 'content', 'response', 'facts']) {
    final text = _qaScalarText(json[key]);
    if (text.isNotEmpty) return text;
  }
  final sections = <String>[];
  for (final entry in const [MapEntry('Fact', 'fact'), MapEntry('Inference', 'inference'), MapEntry('Suggestion', 'suggestion')]) {
    final text = _qaScalarText(json[entry.value]);
    if (text.isNotEmpty) sections.add('${entry.key}: $text');
  }
  return sections.join('\n\n');
}

String _qaScalarText(Object? value) {
  if (value == null) return '';
  if (value is String) return value.trim();
  if (value is List) {
    return value.map(_qaScalarText).where((item) => item.isNotEmpty).join('\n');
  }
  if (value is Map) {
    for (final key in const ['statement', 'content', 'answer', 'text', 'reasoning', 'reason']) {
      final text = _qaScalarText(value[key]);
      if (text.isNotEmpty) return text;
    }
    final parts = <String>[];
    value.forEach((key, item) {
      if (key == 'evidence') return;
      final text = _qaScalarText(item);
      if (text.isNotEmpty) parts.add(text);
    });
    return parts.join(' ');
  }
  return value.toString().trim();
}

class QaEvidence {
  QaEvidence({
    required this.chapterId,
    required this.chapterOrder,
    required this.chapterTitle,
    required this.quote,
    required this.supports,
  });

  final int chapterId;
  final int chapterOrder;
  final String chapterTitle;
  final String quote;
  final String supports;

  factory QaEvidence.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    return QaEvidence(
      chapterId: (map['chapter_id'] as num?)?.toInt() ?? 0,
      chapterOrder: (map['chapter_order'] as num?)?.toInt() ?? 0,
      chapterTitle: map['chapter_title'] as String? ?? '',
      quote: map['quote'] as String? ?? map['source_quote'] as String? ?? '',
      supports: map['supports'] as String? ?? '',
    );
  }
}

class DuplicateCandidate {
  DuplicateCandidate({
    required this.nameA,
    required this.nameB,
    required this.reason,
  });

  final String nameA;
  final String nameB;
  final String reason;

  factory DuplicateCandidate.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    return DuplicateCandidate(
      nameA: map['name_a'] as String? ?? '',
      nameB: map['name_b'] as String? ?? '',
      reason: map['reason'] as String? ?? '',
    );
  }
}

class CharacterListResult {
  CharacterListResult({
    required this.status,
    required this.characters,
    required this.cacheHit,
    required this.persistedFacts,
    required this.provenance,
    this.jobId,
    this.duplicateCandidates = const <DuplicateCandidate>[],
  });

  final String status;
  final List<CharacterItem> characters;
  final bool cacheHit;
  final int persistedFacts;
  final ModelProvenance provenance;
  final int? jobId;
  final List<DuplicateCandidate> duplicateCandidates;

  factory CharacterListResult.fromJson(Map<String, dynamic> json) {
    final rawChars = json['characters'];
    final List<CharacterItem> chars;
    if (rawChars is List) {
      chars = rawChars.map((e) => CharacterItem.fromJson(e)).toList();
    } else {
      chars = <CharacterItem>[];
    }
    final rawCandidates = json['duplicate_candidates'];
    final List<DuplicateCandidate> candidates;
    if (rawCandidates is List) {
      candidates = rawCandidates.map((e) => DuplicateCandidate.fromJson(e)).toList();
    } else {
      candidates = <DuplicateCandidate>[];
    }
    return CharacterListResult(
      status: json['status'] as String? ?? '',
      characters: chars,
      cacheHit: json['cache_hit'] == true,
      persistedFacts: (json['persisted_facts'] as num?)?.toInt() ?? 0,
      provenance: ModelProvenance.fromResponse(json),
      jobId: (json['job_id'] as num?)?.toInt(),
      duplicateCandidates: candidates,
    );
  }
}

class CharacterEvidence {
  CharacterEvidence({
    required this.chapterId,
    required this.chapterOrder,
    required this.chapterTitle,
    required this.sourceQuote,
  });

  final int chapterId;
  final int chapterOrder;
  final String chapterTitle;
  final String sourceQuote;

  factory CharacterEvidence.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    return CharacterEvidence(
      chapterId: (map['chapter_id'] as num?)?.toInt() ?? 0,
      chapterOrder: (map['chapter_order'] as num?)?.toInt() ?? 0,
      chapterTitle: map['chapter_title'] as String? ?? '',
      sourceQuote: map['source_quote'] as String? ?? '',
    );
  }
}

class CharacterAttribute {
  CharacterAttribute({
    required this.attribute,
    required this.value,
    required this.evidence,
  });

  final String attribute;
  final String value;
  final List<CharacterEvidence> evidence;

  factory CharacterAttribute.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    final rawEvidence = map['evidence'];
    final List<CharacterEvidence> evidenceList;
    if (rawEvidence is List) {
      evidenceList = rawEvidence.map((e) => CharacterEvidence.fromJson(e)).toList();
    } else {
      evidenceList = <CharacterEvidence>[];
    }
    return CharacterAttribute(
      attribute: map['attribute'] as String? ?? '',
      value: map['value'] as String? ?? '',
      evidence: evidenceList,
    );
  }
}

class CharacterItem {
  CharacterItem({
    required this.name,
    required this.roleType,
    required this.description,
    required this.aliases,
    required this.sourceChapters,
    required this.evidence,
    required this.confidence,
    required this.reviewStatus,
    this.attributes = const <CharacterAttribute>[],
  });

  final String name;
  final String roleType;
  final String description;
  final List<String> aliases;
  final List<int> sourceChapters;
  final List<CharacterEvidence> evidence;
  final String confidence;
  final String reviewStatus;
  final List<CharacterAttribute> attributes;

  factory CharacterItem.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    final rawEvidence = map['evidence'];
    final List<CharacterEvidence> evidenceList;
    if (rawEvidence is List) {
      evidenceList = rawEvidence.map((e) => CharacterEvidence.fromJson(e)).toList();
    } else {
      evidenceList = <CharacterEvidence>[];
    }
    final rawAliases = map['aliases'];
    final List<String> aliasesList;
    if (rawAliases is List) {
      aliasesList = rawAliases.map((e) => e.toString()).toList();
    } else {
      aliasesList = <String>[];
    }
    final rawAttributes = map['attributes'];
    final List<CharacterAttribute> attributesList;
    if (rawAttributes is List) {
      attributesList = rawAttributes.map((e) => CharacterAttribute.fromJson(e)).toList();
    } else {
      attributesList = <CharacterAttribute>[];
    }
    final rawChapters = map['source_chapters'];
    final List<int> chaptersList;
    if (rawChapters is List) {
      chaptersList = rawChapters.whereType<num>().map((e) => e.toInt()).toList();
    } else {
      chaptersList = <int>[];
    }
    return CharacterItem(
      name: map['name'] as String? ?? 'Unknown',
      roleType: map['role_type'] as String? ?? 'unknown',
      description: map['description'] as String? ?? '',
      aliases: aliasesList,
      sourceChapters: chaptersList,
      evidence: evidenceList,
      confidence: map['confidence'] as String? ?? 'low',
      reviewStatus: map['status'] as String? ?? 'pending_review',
      attributes: attributesList,
    );
  }
}


class RelationshipGraphResult {
  RelationshipGraphResult({
    required this.novelId,
    required this.nodes,
    required this.edges,
  });

  final int novelId;
  final List<RelationshipNode> nodes;
  final List<RelationshipEdge> edges;

  factory RelationshipGraphResult.fromJson(Map<String, dynamic> json) {
    final rawNodes = json['nodes'];
    final rawEdges = json['edges'];
    return RelationshipGraphResult(
      novelId: (json['novel_id'] as num?)?.toInt() ?? 0,
      nodes: rawNodes is List
          ? rawNodes.map((e) => RelationshipNode.fromJson(e)).toList()
          : <RelationshipNode>[],
      edges: rawEdges is List
          ? rawEdges.map((e) => RelationshipEdge.fromJson(e)).toList()
          : <RelationshipEdge>[],
    );
  }
}

class RelationshipNode {
  RelationshipNode({
    required this.name,
    required this.confidence,
    required this.status,
    this.factId,
  });

  final String name;
  final String confidence;
  final String status;
  final int? factId;

  factory RelationshipNode.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    return RelationshipNode(
      name: map['name'] as String? ?? '',
      confidence: map['confidence'] as String? ?? 'low',
      status: map['status'] as String? ?? 'pending_review',
      factId: (map['fact_id'] as num?)?.toInt(),
    );
  }
}

class RelationshipEvolution {
  RelationshipEvolution({this.chapterOrder, required this.relationLabel, required this.event});

  final int? chapterOrder;
  final String relationLabel;
  final String event;

  factory RelationshipEvolution.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    return RelationshipEvolution(
      chapterOrder: (map['chapter_order'] as num?)?.toInt(),
      relationLabel: map['relation_label'] as String? ?? '',
      event: map['event'] as String? ?? '',
    );
  }
}

class RelationshipEdge {
  RelationshipEdge({
    required this.id,
    required this.source,
    required this.target,
    required this.relationType,
    this.relationLabel = '',
    this.attitude = '',
    this.evolution = const [],
    required this.description,
    required this.confidence,
    required this.status,
    required this.sourceQuote,
    required this.chapterTitle,
    this.chapterId,
    this.chapterOrder,
  });

  final int id;
  final String source;
  final String target;
  final String relationType;
  final String relationLabel;
  final String attitude;
  final List<RelationshipEvolution> evolution;
  final String description;
  final String confidence;
  final String status;
  final String sourceQuote;
  final String chapterTitle;
  final int? chapterId;
  final int? chapterOrder;

  factory RelationshipEdge.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    final rawEvolution = map['evolution'];
    return RelationshipEdge(
      id: (map['id'] as num?)?.toInt() ?? 0,
      source: map['source'] as String? ?? '',
      target: map['target'] as String? ?? '',
      relationType: map['relation_type'] as String? ?? 'related',
      relationLabel: map['relation_label'] as String? ?? '',
      attitude: map['attitude'] as String? ?? '',
      evolution: rawEvolution is List
          ? rawEvolution.map((e) => RelationshipEvolution.fromJson(e)).toList()
          : const <RelationshipEvolution>[],
      description: map['description'] as String? ?? '',
      confidence: map['confidence'] as String? ?? 'low',
      status: map['status'] as String? ?? 'pending_review',
      sourceQuote: map['source_quote'] as String? ?? '',
      chapterTitle: map['chapter_title'] as String? ?? '',
      chapterId: (map['chapter_id'] as num?)?.toInt(),
      chapterOrder: (map['chapter_order'] as num?)?.toInt(),
    );
  }
}
class ExtractedFact {
  ExtractedFact({
    required this.id,
    required this.novelId,
    required this.factType,
    required this.content,
    required this.entities,
    this.chapterId,
    this.chunkId,
    required this.sourceQuote,
    required this.confidence,
    required this.status,
    this.modelRunId,
    this.evidence = const [],
    this.extra = const {},
  });

  final int id;
  final int novelId;
  final String factType;
  final String content;
  final List<String> entities;
  final int? chapterId;
  final int? chunkId;
  final String sourceQuote;
  final String confidence;
  final String status;
  final int? modelRunId;
  final List<Map<String, dynamic>> evidence;
  final Map<String, dynamic> extra;

  factory ExtractedFact.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    final rawEntities = map['entities'];
    final entities = rawEntities is List ? rawEntities.map((e) => e.toString()).toList() : <String>[];
    return ExtractedFact(
      id: (map['id'] as num).toInt(),
      novelId: (map['novel_id'] as num?)?.toInt() ?? 0,
      factType: map['fact_type'] as String? ?? '',
      content: map['content'] as String? ?? '',
      entities: entities,
      chapterId: (map['chapter_id'] as num?)?.toInt(),
      chunkId: (map['chunk_id'] as num?)?.toInt(),
      sourceQuote: map['source_quote'] as String? ?? '',
      confidence: map['confidence'] as String? ?? 'low',
      status: map['status'] as String? ?? 'pending_review',
      modelRunId: (map['model_run_id'] as num?)?.toInt(),
      evidence: map['evidence'] is List
          ? (map['evidence'] as List)
              .map((item) => Map<String, dynamic>.from(item as Map))
              .toList()
          : const [],
      extra: map['extra'] is Map ? Map<String, dynamic>.from(map['extra'] as Map) : const {},
    );
  }
}

class ReviewAction {
  ReviewAction({
    required this.id,
    required this.recordType,
    required this.recordId,
    required this.fromStatus,
    required this.toStatus,
    required this.note,
  });

  final int id;
  final String recordType;
  final int recordId;
  final String fromStatus;
  final String toStatus;
  final String note;

  factory ReviewAction.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    return ReviewAction(
      id: (map['id'] as num).toInt(),
      recordType: map['record_type'] as String? ?? '',
      recordId: (map['record_id'] as num?)?.toInt() ?? 0,
      fromStatus: map['from_status'] as String? ?? '',
      toStatus: map['to_status'] as String? ?? '',
      note: map['note'] as String? ?? '',
    );
  }
}

class ReviewUpdateResult {
  ReviewUpdateResult({required this.fact, required this.reviewActions});

  final ExtractedFact fact;
  final List<ReviewAction> reviewActions;

  factory ReviewUpdateResult.fromJson(Map<String, dynamic> json) {
    final actions = json['review_actions'];
    return ReviewUpdateResult(
      fact: ExtractedFact.fromJson(json),
      reviewActions: actions is List ? actions.map((item) => ReviewAction.fromJson(item)).toList() : <ReviewAction>[],
    );
  }
}


class AnalysisJob {
  AnalysisJob({
    required this.id,
    this.novelId,
    this.chapterId,
    required this.taskType,
    required this.status,
    required this.progress,
    required this.error,
    required this.retryCount,
    required this.resultCacheKey,
    required this.requestJson,
    required this.requestedModel,
    required this.effectiveModel,
    required this.cacheSource,
    required this.modelError,
    required this.providerCallAttempted,
    required this.providerCallSucceeded,
    required this.localFallback,
    required this.createdAt,
    required this.updatedAt,
  });

  final int id;
  final int? novelId;
  final int? chapterId;
  final String taskType;
  final String status;
  final int progress;
  final String error;
  final int retryCount;
  final String resultCacheKey;
  final String requestJson;
  final String requestedModel;
  final String effectiveModel;
  final String cacheSource;
  final String modelError;
  final bool providerCallAttempted;
  final bool providerCallSucceeded;
  final bool localFallback;
  final String createdAt;
  final String updatedAt;

  factory AnalysisJob.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    return AnalysisJob(
      id: (map['id'] as num).toInt(),
      novelId: (map['novel_id'] as num?)?.toInt(),
      chapterId: (map['chapter_id'] as num?)?.toInt(),
      taskType: map['task_type'] as String? ?? '',
      status: map['status'] as String? ?? '',
      progress: (map['progress'] as num?)?.toInt() ?? 0,
      error: map['error'] as String? ?? '',
      retryCount: (map['retry_count'] as num?)?.toInt() ?? 0,
      resultCacheKey: map['result_cache_key'] as String? ?? '',
      requestJson: map['request_json'] as String? ?? '{}',
      requestedModel: map['requested_model'] as String? ?? '',
      effectiveModel: map['effective_model'] as String? ?? '',
      cacheSource: map['cache_source'] as String? ?? '',
      modelError: map['model_error'] as String? ?? '',
      providerCallAttempted: map['provider_call_attempted'] == true,
      providerCallSucceeded: map['provider_call_succeeded'] == true,
      localFallback: map['local_fallback'] == true,
      createdAt: map['created_at'] as String? ?? '',
      updatedAt: map['updated_at'] as String? ?? '',
    );
  }
}

class JobStartResult {
  JobStartResult({
    required this.jobId,
    required this.status,
    required this.duplicated,
    required this.effectiveModel,
  });

  final int jobId;
  final String status;
  final bool duplicated;
  final String effectiveModel;

  factory JobStartResult.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    return JobStartResult(
      jobId: (map['job_id'] as num).toInt(),
      status: map['status'] as String? ?? '',
      duplicated: map['duplicated'] == true,
      effectiveModel: map['effective_model'] as String? ?? '',
    );
  }
}

class JobResultResponse {
  JobResultResponse({
    required this.jobId,
    required this.status,
    this.result,
    this.provenance,
    this.error,
    this.progress,
    this.effectiveModel,
  });

  final int jobId;
  final String status;
  final Map<String, dynamic>? result;
  final Map<String, dynamic>? provenance;
  final String? error;
  final int? progress;
  final String? effectiveModel;

  Map<String, dynamic>? mergedResult() {
    final raw = result;
    if (raw == null) return null;
    final merged = Map<String, dynamic>.from(raw);
    if (provenance != null && merged['provenance'] is! Map<String, dynamic>) {
      merged['provenance'] = provenance;
    }
    merged['job_id'] ??= jobId;
    if (provenance != null) {
      merged['source'] ??= provenance!['source'];
      merged['cache_hit'] ??= provenance!['cache_hit'];
      merged['cache_key'] ??= provenance!['cache_key'];
      merged['model_error'] ??= provenance!['model_error'];
    }
    return merged;
  }

  factory JobResultResponse.fromJson(Object? json) {
    final map = json as Map<String, dynamic>;
    final rawResult = map['result'];
    final rawProvenance = map['provenance'];
    return JobResultResponse(
      jobId: (map['job_id'] as num).toInt(),
      status: map['status'] as String? ?? '',
      result: rawResult is Map<String, dynamic> ? rawResult : null,
      provenance: rawProvenance is Map<String, dynamic> ? rawProvenance : null,
      error: map['error'] as String?,
      progress: (map['progress'] as num?)?.toInt(),
      effectiveModel: map['effective_model'] as String?,
    );
  }
}
