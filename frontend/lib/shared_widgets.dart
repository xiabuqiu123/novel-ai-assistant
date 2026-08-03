import 'package:flutter/material.dart';

import 'package:frontend/api_client.dart';

class ChapterSummaryResultPanel extends StatelessWidget {
  const ChapterSummaryResultPanel({super.key, required this.result});

  final ChapterSummaryResult result;

  @override
  Widget build(BuildContext context) {
    return Card(
      key: const Key('chapter-summary-result'),
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('摘要', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Text(result.shortSummary.isEmpty ? '未返回摘要' : result.shortSummary),
            if (result.keyEvents.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text('关键事件', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: 4),
              ...result.keyEvents.map((event) => Text('- $event')),
            ],
            const SizedBox(height: 12),
            ProvenancePanel(status: result.status, provenance: result.provenance),
          ],
        ),
      ),
    );
  }
}

class ProvenancePanel extends StatelessWidget {
  const ProvenancePanel({super.key, required this.status, required this.provenance});

  final String status;
  final ModelProvenance provenance;

  @override
  Widget build(BuildContext context) {
    final warning = provenance.localFallback || provenance.source.contains('fallback') || provenance.source == 'mixed';
    final color = warning ? Colors.orange : Colors.green;
    return Container(
      key: const Key('provenance-panel'),
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: color.withAlpha(24),
        border: Border.all(color: color.withAlpha(90)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('状态: $status  |  来源: ${provenance.source.isEmpty ? '未知' : provenance.source}'),
          Text('缓存命中: ${provenance.cacheHit ? '是' : '否'}  |  模型: ${provenance.modelUsed.isEmpty ? '未知' : provenance.modelUsed}'),
          Text('服务商调用: ${provenance.providerCallAttempted ? '已尝试' : '未尝试'}  |  成功: ${provenance.providerCallSucceeded ? '是' : '否'}'),
          if (provenance.jobId != null) Text('任务 ID: ${provenance.jobId}'),
          if (provenance.modelError.isNotEmpty) Text('模型错误: ${provenance.modelError}'),
          if (provenance.source == 'mixed') ...[
            const SizedBox(height: 6),
            const Text('部分成功（有批次走了本地兜底，建议重跑）'),
          ],
          if (warning) ...[
            const SizedBox(height: 6),
            const Text('使用了本地兜底结果, 内容为启发式生成, 使用前请人工复核。'),
          ],
        ],
      ),
    );
  }
}

class EmptyBookshelfState extends StatelessWidget {
  const EmptyBookshelfState({super.key});

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 32),
      child: Center(child: Text('尚未导入小说')),
    );
  }
}

class ErrorState extends StatelessWidget {
  const ErrorState({super.key, required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('请求失败', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Text(message),
            const SizedBox(height: 12),
            IconButton.filled(
              tooltip: '重试',
              onPressed: onRetry,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
      ),
    );
  }
}

const Map<String, String> factStatusLabels = <String, String>{
  'pending_review': '待复核',
  'confirmed': '已确认',
  'dismissed': '已驳回',
  'explained': '已解释',
  'watching': '观察中',
  'active': '生效中',
  'superseded': '已取代',
  'updated': '已更新',
  'contradicted': '存在冲突',
};

String displayFactStatus(String status) => factStatusLabels[status] ?? status;

const Map<String, String> confidenceLabels = <String, String>{
  'high': '高',
  'medium': '中',
  'low': '低',
};

String displayConfidence(String confidence) => confidenceLabels[confidence] ?? confidence;

const Map<String, String> jobStatusLabels = <String, String>{
  'queued': '排队中',
  'running': '运行中',
  'completed': '已完成',
  'failed': '已失败',
  'cancelled': '已取消',
};

String displayJobStatus(String status) => jobStatusLabels[status] ?? status;

class MarkdownExportResult {
  const MarkdownExportResult({
    required this.filename,
    required this.contentType,
    required this.markdown,
  });

  final String filename;
  final String contentType;
  final String markdown;

  factory MarkdownExportResult.fromJson(Map<String, dynamic> json) {
    return MarkdownExportResult(
      filename: json['filename'] as String? ?? 'novel.md',
      contentType: json['content_type'] as String? ?? 'text/markdown',
      markdown: json['markdown'] as String? ?? json['content'] as String? ?? '',
    );
  }
}


class MarkdownExportPanel extends StatelessWidget {
  const MarkdownExportPanel({super.key, required this.result});

  final MarkdownExportResult result;

  /// 预览最多显示前 2000 字，避免长报告把整段塞进 SelectableText 卡死界面（A8）。
  static const int previewLimit = 2000;

  @override
  Widget build(BuildContext context) {
    final full = result.markdown;
    final truncated = full.length > previewLimit;
    final preview = truncated ? full.substring(0, previewLimit) : full;
    return Card(
      key: const Key('markdown-export-result'),
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Markdown 导出', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Text('文件名: ${result.filename}'),
            Text('内容类型: ${result.contentType}'),
            const SizedBox(height: 12),
            SelectableText(
              full.isEmpty ? '未返回 Markdown 内容' : preview,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(height: 1.5),
            ),
            if (truncated) ...[
              const SizedBox(height: 8),
              Text(
                '已省略后续内容（共 ${full.length} 字），请保存文件查看完整内容。',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ],
        ),
      ),
    );
  }
}


