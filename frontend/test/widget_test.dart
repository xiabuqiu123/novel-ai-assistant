import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:frontend/api_client.dart';
import 'package:frontend/main.dart';
import 'package:frontend/settings_page.dart';
import 'package:frontend/timeline_page.dart';
import 'package:frontend/conflict_page.dart';

void main() {
  test('qa result parses structured fact inference suggestion payloads', () {
    final result = QaResult.fromJson({
      'status': 'ok',
      'fact': [
        {'statement': 'Sun Wukong challenged heaven.', 'evidence': 'He shouted upward.', 'chapter_id': 2},
      ],
      'inference': 'He is defiant.',
      'suggestion': 'Review later chapters for consequences.',
      'evidence': [
        {'chapter_id': 2, 'chapter_title': 'Second Chapter', 'quote': 'He shouted upward.'},
      ],
    });

    expect(result.answer, contains('Fact: Sun Wukong challenged heaven.'));
    expect(result.answer, contains('Inference: He is defiant.'));
    expect(result.answer, contains('Suggestion: Review later chapters for consequences.'));
    expect(result.evidence.single.chapterId, 2);
  });

  test('qa result parses nested parsed_json answer payloads', () {
    final result = QaResult.fromJson({
      'status': 'ok',
      'parsed_json': {
        'answer': 'Nested answer text.',
        'evidence': [
          {'chapter_id': 3, 'source_quote': 'Nested quote.'},
        ],
      },
    });

    expect(result.answer, 'Nested answer text.');
    expect(result.evidence.single.quote, 'Nested quote.');
  });

  test('qa result reports unusable successful payloads clearly', () {
    final result = QaResult.fromJson({'status': 'ok', 'raw_json': '{}'});

    expect(result.answer, 'Model returned no usable answer fields.');
  });

  test('extracted fact and review result parse review metadata', () {
    final fact = ExtractedFact.fromJson({
      'id': 7,
      'novel_id': 1,
      'fact_type': 'character_profile',
      'content': 'Li Qing: protagonist',
      'entities': ['Li Qing'],
      'chapter_id': 2,
      'source_quote': 'Li Qing entered town.',
      'confidence': 'medium',
      'status': 'confirmed',
      'model_run_id': 9,
    });
    final review = ReviewUpdateResult.fromJson({
      'id': 7,
      'novel_id': 1,
      'fact_type': 'character_profile',
      'content': 'Li Qing: protagonist',
      'entities': ['Li Qing'],
      'source_quote': 'Li Qing entered town.',
      'confidence': 'medium',
      'status': 'confirmed',
      'review_actions': [
        {
          'id': 1,
          'record_type': 'extracted_fact',
          'record_id': 7,
          'from_status': 'pending_review',
          'to_status': 'confirmed',
          'note': 'checked',
        },
      ],
    });

    expect(fact.entities, ['Li Qing']);
    expect(fact.status, 'confirmed');
    expect(review.fact.id, 7);
    expect(review.reviewActions.single.toStatus, 'confirmed');
  });

  test('backend connection troubleshooting explains real phone LAN failures', () {
    final emulatorMessage = backendConnectionTroubleshootingMessage(
      'http://10.0.2.2:8000',
      ApiException(0, 'Connection refused'),
    );
    final localhostMessage = backendConnectionTroubleshootingMessage(
      '127.0.0.1:8000',
      ApiException(0, 'Connection refused'),
    );

    expect(emulatorMessage, contains('10.0.2.2 仅适用于安卓模拟器'));
    expect(emulatorMessage, contains('.\\run_backend.ps1'));
    expect(emulatorMessage, contains('Windows 防火墙'));
    expect(localhostMessage, contains('127.0.0.1/localhost 指向手机本身'));
    expect(localhostMessage, contains('http://127.0.0.1:8000'));
  });

  testWidgets('home screen shows MVP entries', (WidgetTester tester) async {
    await tester.pumpWidget(NovelAnalysisApp(apiClient: FakeNovelApiClient()));

    expect(find.text('书镜辨章'), findsOneWidget);
    expect(find.text('书架'), findsOneWidget);
    expect(find.text('导入 TXT'), findsOneWidget);
    expect(find.text('模型设置'), findsOneWidget);
    expect(find.text('后端连接'), findsOneWidget);
  });

  testWidgets('backend connection screen can save backend url', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    String? savedUrl;
    await tester.pumpWidget(
      MaterialApp(
        home: BackendConnectionScreen(
          apiClient: client,
          backendBaseUrl: 'http://10.0.2.2:8000',
          onBackendBaseUrlChanged: (value) async => savedUrl = value,
        ),
      ),
    );

    expect(find.text('后端连接'), findsWidgets);
    expect(find.byKey(const Key('backend-url-field')), findsOneWidget);
    await tester.enterText(find.byKey(const Key('backend-url-field')), 'http://192.168.1.50:8000');
    await tester.tap(find.byKey(const Key('save-backend-url-button')));
    await tester.pumpAndSettle();

    expect(find.textContaining('后端地址已保存'), findsOneWidget);
    expect(find.textContaining('http://192.168.1.50:8000'), findsWidgets);
    expect(savedUrl, 'http://192.168.1.50:8000');
  });

  testWidgets('bookshelf screen shows backend status and novels', (WidgetTester tester) async {
    await tester.pumpWidget(NovelAnalysisApp(apiClient: FakeNovelApiClient()));

    await tester.tap(find.text('书架'));
    await tester.pumpAndSettle();

    expect(find.text('后端状态: ok'), findsOneWidget);
    expect(find.text('我的小说'), findsOneWidget);
    expect(find.text('Sample Novel'), findsOneWidget);
    expect(find.text('3 章, 5 个分块, utf-8'), findsOneWidget);
  });

  testWidgets('chapter list shows outline and markdown tools', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(NovelAnalysisApp(apiClient: client));

    await tester.tap(find.text('书架'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Sample Novel'));
    await tester.pumpAndSettle();

    expect(find.text('章节列表'), findsOneWidget);
    expect(find.text('生成章纲'), findsOneWidget);
    expect(find.text('Opening Moves'), findsOneWidget);
    expect(find.text('128 字'), findsOneWidget);

    // Tap generate outline - starts async job flow
    await tester.tap(find.byKey(const Key('generate-outline-button')));
    await tester.pump(); // process button tap + startOutline called
    await tester.pump(); // startOutline completes, timer created
    await tester.pump(const Duration(seconds: 3)); // timer fires at t=2s
    await tester.pump(); // getAnalysisJob completes
    await tester.pump(); // getJobResult completes, setState
    await tester.pump(); // rebuild

    expect(client.startOutlineNovelId, 1);
    expect(find.byKey(const Key('book-outline-result')), findsOneWidget);
    expect(find.text('Local chapter-order outline'), findsOneWidget);
    expect(find.text('状态: local_fallback  |  来源: local_fallback'), findsOneWidget);

    // Force refresh: tap and check the flag is set
    await tester.tap(find.byKey(const Key('force-refresh-outline-button')));
    await tester.pump();
    await tester.pump();
    await tester.pump(const Duration(seconds: 3));
    await tester.pump();
    await tester.pump();
    await tester.pump();
    expect(client.startOutlineForceRefresh, true);

    await tester.tap(find.byKey(const Key('clear-outline-cache-button')));
    await tester.pumpAndSettle();
    expect(client.clearedCacheNovelId, 1);
    expect(client.clearedCacheTaskType, 'book_outline');
    expect(find.byKey(const Key('book-outline-result')), findsNothing);

  });

  testWidgets('chapter list shows whole-book stage outline cards', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(NovelAnalysisApp(apiClient: client));

    await tester.tap(find.text('书架'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Sample Novel'));
    await tester.pumpAndSettle();

    expect(find.text('生成大纲'), findsOneWidget);

    await tester.tap(find.byKey(const Key('generate-stage-outline-button')));
    await tester.pump();
    await tester.pump();
    await tester.pump(const Duration(seconds: 3));
    await tester.pump();
    await tester.pump();
    await tester.pump();

    expect(client.startStageOutlineNovelId, 1);
    expect(find.byKey(const Key('book-stage-outline-result')), findsOneWidget);
    expect(find.byKey(const Key('stage-card-1')), findsOneWidget);
    expect(find.text('闹天宫'), findsOneWidget);
    expect(find.text('第 1-2 章'), findsOneWidget);

    // Evidence section present (collapsed ExpansionTile title still visible)
    expect(find.byKey(const Key('stage-outline-evidence')), findsOneWidget);
    expect(find.text('证据（1 条）'), findsOneWidget);
    // Expand and verify the source quote renders
    await tester.tap(find.byKey(const Key('stage-outline-evidence')));
    await tester.pumpAndSettle();
    expect(find.text('第 1 章'), findsOneWidget);
    expect(find.text('孙悟空打到凌霄宝殿外，众神不能敌。'), findsOneWidget);

    await tester.tap(find.byKey(const Key('clear-stage-outline-cache-button')));
    await tester.pumpAndSettle();
    expect(client.clearedCacheTaskType, 'book_stage_outline');
    expect(find.byKey(const Key('book-stage-outline-result')), findsNothing);
  });

  testWidgets('chapter reader shows content and summary result', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(
      MaterialApp(
        home: ChapterReaderScreen(
          apiClient: client,
          chapterSummary: ChapterSummary(
            id: 10,
            order: 1,
            title: 'Opening Moves',
            charCount: 128,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('第 1 章 · 共 47 字'), findsOneWidget);
    expect(find.text('Li Qing arrives at Qingshi Town and meets Wang.'), findsOneWidget);

    await tester.tap(find.byKey(const Key('summarize-chapter-button')));
    await tester.pump(); // process button tap + startChapterSummary called
    await tester.pump(); // startChapterSummary completes, poll loop starts
    await tester.pump(const Duration(seconds: 3)); // first poll delay elapses
    await tester.pump(); // getAnalysisJob completes
    await tester.pump(); // getJobResult completes, setState
    await tester.pump(); // rebuild

    expect(client.startSummaryChapterId, 10);
    expect(find.byKey(const Key('chapter-summary-result')), findsOneWidget);
    expect(find.text('摘要'), findsOneWidget);
    expect(find.text('Li Qing reaches town and receives a warning.'), findsOneWidget);
    expect(find.text('关键事件'), findsOneWidget);
    expect(find.text('- Li Qing arrives at Qingshi Town'), findsOneWidget);
    expect(find.text('状态: local_fallback  |  来源: local_fallback'), findsOneWidget);
    expect(find.text('缓存命中: 否  |  模型: gpt-test'), findsOneWidget);
    expect(find.text('任务 ID: 98'), findsOneWidget);

    await tester.tap(find.byKey(const Key('force-refresh-chapter-summary-button')));
    await tester.pump();
    await tester.pump();
    await tester.pump(const Duration(seconds: 3));
    await tester.pump();
    await tester.pump();
    await tester.pump();

    expect(client.startSummaryForceRefresh, true);
  });

  testWidgets('qa force refresh chip sends cache bypass flag', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(
      MaterialApp(
        home: QaScreen(
          apiClient: client,
          novel: Novel(
            id: 1,
            title: 'Sample Novel',
            chapterCount: 3,
            chunkCount: 5,
            encoding: 'utf-8',
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'Where did Li Qing arrive?');
    await tester.tap(find.byKey(const Key('qa-force-refresh-chip')));
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(client.askedQuestion, 'Where did Li Qing arrive?');
    expect(client.qaForceRefresh, true);
    expect(find.text('Li Qing arrived at Qingshi Town.'), findsWidgets);
  });

  testWidgets('qa cancel button cancels the running job and stops waiting', (WidgetTester tester) async {
    final client = FakeNovelApiClient()..qaRunning = true;
    await tester.pumpWidget(
      MaterialApp(
        home: QaScreen(
          apiClient: client,
          novel: Novel(
            id: 1,
            title: 'Sample Novel',
            chapterCount: 3,
            chunkCount: 5,
            encoding: 'utf-8',
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'Where did Li Qing arrive?');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pump(); // startQa 完成
    await tester.pump(); // 轮询首轮进入等待
    expect(find.byKey(const Key('qa-cancel-button')), findsOneWidget);

    await tester.tap(find.byKey(const Key('qa-cancel-button')));
    await tester.pump(); // cancel 请求发出
    expect(client.cancelledJobId, 101);
    await tester.pump(const Duration(seconds: 2)); // 轮询唤醒并发现取消
    await tester.pump(); // 渲染错误状态
    expect(find.textContaining('已取消问答任务'), findsOneWidget);
  });

  testWidgets('characters page polls existing running job without starting a new one', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..includeExtractionJobs = true
      ..extractionJobRunning = true;
    await tester.pumpWidget(
      MaterialApp(
        home: CharacterProfilesScreen(
          apiClient: client,
          novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
        ),
      ),
    );
    await tester.pump();
    await tester.pump();
    // 只读进入：未发起新任务，跟随已有任务轮询。
    expect(client.startCharactersNovelId, isNull);
    expect(find.textContaining('任务 #100'), findsOneWidget);

    client.extractionJobRunning = false;
    await tester.pump(const Duration(seconds: 2)); // 轮询唤醒
    await tester.pump(); // getAnalysisJob 完成
    await tester.pump(); // getJobResult 完成
    await tester.pumpAndSettle();

    expect(find.text('共找到 1 个人物'), findsOneWidget);
    expect(find.text('Li Qing'), findsOneWidget);
  });

  testWidgets('character cards show role description and evidence labels', (WidgetTester tester) async {
    final client = FakeNovelApiClient()..includeExtractionJobs = true;
    await tester.pumpWidget(
      MaterialApp(
        home: CharacterProfilesScreen(
          apiClient: client,
          novel: Novel(
            id: 1,
            title: 'Sample Novel',
            chapterCount: 3,
            chunkCount: 5,
            encoding: 'utf-8',
          ),
        ),
      ),
    );

    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(find.text('人物: Sample Novel'), findsOneWidget);
    expect(find.text('共找到 1 个人物'), findsOneWidget);
    expect(find.text('Li Qing'), findsOneWidget);
    expect(find.text('supporting'), findsOneWidget);
    expect(find.text('A test character.'), findsOneWidget);
    await tester.tap(find.text('Li Qing'));
    await tester.pumpAndSettle();
    expect(find.text('来源章节: 1'), findsOneWidget);
    expect(find.text('第 1 章: Opening Moves'), findsOneWidget);
  });
  testWidgets('character cards render attribute groups with all evidence', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..returnAttributeCharacter = true
      ..includeExtractionJobs = true;
    await tester.pumpWidget(
      MaterialApp(
        home: CharacterProfilesScreen(
          apiClient: client,
          novel: Novel(
            id: 1,
            title: 'Sample Novel',
            chapterCount: 9,
            chunkCount: 5,
            encoding: 'utf-8',
          ),
        ),
      ),
    );
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
    expect(find.text('共找到 1 个人物'), findsOneWidget);
    expect(find.text('齐天大圣'), findsOneWidget);
    await tester.tap(find.text('齐天大圣'));
    await tester.pumpAndSettle();
    // Five attribute group labels render.
    expect(find.text('外貌：毛脸雷公嘴'), findsOneWidget);
    expect(find.text('性格：桀骜不驯'), findsOneWidget);
    expect(find.text('身份/背景：未提及'), findsOneWidget);
    expect(find.text('能力：七十二变'), findsOneWidget);
    expect(find.text('重要经历：大闹天宫'), findsOneWidget);
    // Full evidence (not truncated) for appearance: two chapter refs + two quotes.
    expect(find.text('第 1 章: 出世'), findsOneWidget);
    expect(find.text('第 5 章: 闹地府'), findsNWidgets(2));
    expect(find.text('石卵迸裂，化作一个石猴。'), findsOneWidget);
    expect(find.text('尖嘴缩腮，毛脸雷公嘴。'), findsOneWidget);
    // Abilities evidence also fully rendered.
    expect(find.text('第 9 章: 拜师'), findsOneWidget);
    expect(find.text('学会七十二般变化。'), findsOneWidget);
    // "第 0 章" bug fixed: missing order/title renders as 未标注章节, not 第 0 章.
    expect(find.text('未标注章节'), findsOneWidget);
    expect(find.text('来源未标注证据条。'), findsOneWidget);
    expect(find.text('第 0 章'), findsNothing);
    expect(find.text('第 0 章: '), findsNothing);
  });
  testWidgets('settings faction card renders structured fields', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..settingsFactsOverride = [
        ExtractedFact(
          id: 71,
          novelId: 1,
          factType: 'faction',
          content: '天庭: 统领三界众神的政权。',
          entities: ['天庭', '神界', '天宫'],
          sourceQuote: '天庭众神听令。',
          confidence: 'high',
          status: 'pending_review',
          extra: {
            'name': '天庭',
            'description': '统领三界众神的政权。',
            'aliases': ['神界', '天宫'],
            'type': '政权',
            'parent': null,
            'sub_organizations': ['御马监', '蟠桃园'],
            'positions': [
              {'title': '玉帝', 'holder': '玉皇大帝', 'holder_intro': '天庭最高统治者。', 'rotation': '无'},
            ],
            'relationships': [
              {'other': '灵山佛门', 'summary': '各有默契'},
            ],
          },
        ),
      ];
    await tester.pumpWidget(MaterialApp(
      home: SettingsScreen(
        apiClient: client,
        novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
        highlightFaction: '天庭',
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('别名：神界、天宫'), findsOneWidget);
    expect(find.text('政权'), findsOneWidget);
    expect(find.text('统领三界众神的政权。'), findsOneWidget);
    expect(find.text('下属机构：御马监、蟠桃园'), findsOneWidget);
    expect(find.text('职位：玉帝'), findsOneWidget);
    expect(find.text('担任者：玉皇大帝'), findsOneWidget);
    expect(find.text('介绍：天庭最高统治者。'), findsOneWidget);
    expect(find.text('轮换：无'), findsOneWidget);
    expect(find.text('灵山佛门：各有默契'), findsOneWidget);
    expect(find.text('原文：天庭众神听令。'), findsOneWidget);
  });

  testWidgets('character card shows affiliation timeline and clickable factions', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..returnAttributeCharacter = true
      ..returnAffiliationCharacter = true
      ..includeExtractionJobs = true;
    await tester.pumpWidget(
      MaterialApp(
        home: CharacterProfilesScreen(
          apiClient: client,
          novel: Novel(
            id: 1,
            title: 'Sample Novel',
            chapterCount: 9,
            chunkCount: 5,
            encoding: 'utf-8',
          ),
        ),
      ),
    );
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(find.text('所属势力：'), findsOneWidget);
    expect(find.text('妖族（第1-9章）→ 取经队伍（第10章起）'), findsOneWidget);
    expect(find.widgetWithText(ActionChip, '妖族'), findsOneWidget);
    expect(find.widgetWithText(ActionChip, '取经队伍'), findsOneWidget);
  });

  testWidgets('character page shows duplicate candidates banner', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..returnDuplicateCandidates = true
      ..includeExtractionJobs = true;
    await tester.pumpWidget(
      MaterialApp(
        home: CharacterProfilesScreen(
          apiClient: client,
          novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
        ),
      ),
    );
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(find.textContaining('疑似重名 1 处'), findsOneWidget);
    expect(find.textContaining('沙僧 / 沙和尚'), findsOneWidget);
  });

  testWidgets('faction position holder navigates to characters page', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..includeExtractionJobs = true
      ..settingsFactsOverride = [
        ExtractedFact(
          id: 72,
          novelId: 1,
          factType: 'faction',
          content: '天庭: 统领三界众神的政权。',
          entities: ['天庭'],
          sourceQuote: '天庭众神听令。',
          confidence: 'high',
          status: 'pending_review',
          extra: {
            'name': '天庭',
            'description': '统领三界众神的政权。',
            'aliases': ['神界'],
            'type': '政权',
            'parent': null,
            'sub_organizations': ['御马监'],
            'positions': [
              {'title': '玉帝', 'holder': '玉皇大帝', 'holder_intro': '天庭最高统治者。', 'rotation': '无'},
            ],
            'relationships': <Object>[],
          },
        ),
      ];
    await tester.pumpWidget(MaterialApp(
      home: SettingsScreen(
        apiClient: client,
        novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
        highlightFaction: '天庭',
      ),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.text('担任者：玉皇大帝'));
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(find.text('人物: Sample Novel'), findsOneWidget);
    expect(client.startCharactersNovelId, isNull);
  });

  testWidgets('character affiliation chip navigates to settings page', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..returnAttributeCharacter = true
      ..returnAffiliationCharacter = true
      ..includeExtractionJobs = true
      ..settingsFactsOverride = [
        ExtractedFact(
          id: 73,
          novelId: 1,
          factType: 'faction',
          content: '妖族: 花果山群妖。',
          entities: ['妖族'],
          sourceQuote: '与众妖结拜。',
          confidence: 'medium',
          status: 'pending_review',
          extra: {
            'name': '妖族',
            'description': '花果山群妖。',
            'aliases': <String>[],
            'type': '种族',
            'parent': null,
            'sub_organizations': <String>[],
            'positions': [
              {'title': '美猴王', 'holder': '孙悟空', 'holder_intro': '花果山之主。', 'rotation': '无'},
            ],
            'relationships': <Object>[],
          },
        ),
      ];
    await tester.pumpWidget(
      MaterialApp(
        home: CharacterProfilesScreen(
          apiClient: client,
          novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 9, chunkCount: 5, encoding: 'utf-8'),
        ),
      ),
    );
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(ActionChip, '妖族'));
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(find.text('设定'), findsOneWidget);
    // 目标势力卡片已展开并显示职位。
    expect(find.text('花果山群妖。'), findsOneWidget);
    expect(find.text('职位：美猴王'), findsOneWidget);
  });

  testWidgets('relationship graph screen shows nodes and edges', (WidgetTester tester) async {
    final client = FakeNovelApiClient()..includeExtractionJobs = true;
    await tester.pumpWidget(
      MaterialApp(
        home: RelationshipGraphScreen(
          apiClient: client,
          novel: Novel(
            id: 1,
            title: 'Sample Novel',
            chapterCount: 3,
            chunkCount: 5,
            encoding: 'utf-8',
          ),
        ),
      ),
    );

    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(client.startRelationshipsNovelId, isNull);
    expect(find.text('关系图: Sample Novel'), findsOneWidget);
    expect(find.text('2 个人物, 1 条关系'), findsOneWidget);
    expect(find.text('Li Qing'), findsOneWidget);
    expect(find.text('Li Qing -[师弟]-> Wang'), findsOneWidget);
    expect(find.text('友好'), findsOneWidget);
    expect(find.textContaining('第 1 章：同门（入门相识）'), findsOneWidget);
    expect(find.text('Travel companions.'), findsOneWidget);
    expect(find.text('第 1 章: Opening Moves'), findsOneWidget);
    expect(find.text('图谱视图（实验性）'), findsOneWidget);
  });
  testWidgets('import txt screen posts pasted content and shows result', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(NovelAnalysisApp(apiClient: client));

    await tester.tap(find.text('导入 TXT'));
    await tester.pumpAndSettle();

    expect(find.text('分块大小: 6000 字符'), findsOneWidget);

    await tester.enterText(find.byKey(const Key('import-title-field')), 'New Novel');
    await tester.enterText(
      find.byKey(const Key('import-text-field')),
      'First chapter\nLi Qing opens the door.',
    );
    await tester.tap(find.byKey(const Key('import-submit-button')));
    await tester.pumpAndSettle();

    expect(client.importedTitle, 'New Novel');
    expect(client.importedText, 'First chapter\nLi Qing opens the door.');
    expect(find.text('导入成功'), findsOneWidget);
    expect(find.text('小说 ID: 2'), findsOneWidget);
    expect(find.text('章节数: 2'), findsOneWidget);
    expect(find.text('分块数: 4'), findsOneWidget);
    expect(find.text('编码: utf-8'), findsOneWidget);
    expect(find.text('查看书架'), findsOneWidget);
  });

  testWidgets('import txt screen shows choose file button', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(NovelAnalysisApp(apiClient: client));

    await tester.tap(find.text('导入 TXT'));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('import-file-button')), findsOneWidget);
    await tester.tap(find.byKey(const Key('import-file-button')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('import-title-field')), findsOneWidget);
  });
  testWidgets('analysis jobs retry runs worker and shows job details', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(
      MaterialApp(home: AnalysisJobsScreen(apiClient: client)),
    );
    await tester.pumpAndSettle();

    expect(find.text('共 1 个任务'), findsOneWidget);
    expect(find.text('任务 #1 - chapter_summary'), findsOneWidget);
    expect(find.text('小说: 1  |  重试次数: 0'), findsOneWidget);
    expect(find.text('缓存: cache-key-1'), findsOneWidget);
    expect(find.text('重试'), findsOneWidget);

    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();

    expect(client.retriedJobId, 1);
    expect(client.ranJobId, 1);

    await tester.tap(find.byTooltip('运行排队任务'));
    await tester.pumpAndSettle();
    expect(client.ranNextJob, true);
  });

  // A10：任务超过 50 条时默认只显示最近 50 条并出现"显示全部"按钮。
  testWidgets('analysis jobs list truncates to recent 50 with show-all toggle', (WidgetTester tester) async {
    final many = <AnalysisJob>[
      for (int i = 1; i <= 60; i++)
        AnalysisJob(
          id: i,
          novelId: 1,
          taskType: 'chapter_summary',
          status: 'completed',
          progress: 100,
          error: '',
          retryCount: 0,
          resultCacheKey: 'k$i',
          requestJson: '{}',
          requestedModel: 'gpt-test',
          effectiveModel: 'gpt-test',
          cacheSource: '',
          modelError: '',
          providerCallAttempted: false,
          providerCallSucceeded: true,
          localFallback: false,
          createdAt: '2026-01-01',
          // 越靠后的任务 updatedAt 越大；展开前应排在最前（固定两位宽，按字符串字典序单调递增）。
          updatedAt: '2026-12-31T23:59:${(i < 10) ? '0' : ''}$i',
        ),
    ];
    final client = FakeNovelApiClient()..listJobsOverride = many;
    await tester.pumpWidget(MaterialApp(home: AnalysisJobsScreen(apiClient: client)));
    await tester.pumpAndSettle();

    expect(find.textContaining('共 60 个任务（仅显示最近 50 条）'), findsOneWidget);
    expect(find.text('显示全部（60 条）'), findsOneWidget);
    // 最近 #60 应在前部可见（按 updatedAt 倒序）。
    expect(find.text('任务 #60 - chapter_summary'), findsOneWidget);

    await tester.tap(find.text('显示全部（60 条）'));
    await tester.pumpAndSettle();
    expect(find.textContaining('共 60 个任务'), findsOneWidget);
    expect(find.text('只看最近 50 条'), findsOneWidget);
    // 展开后最早的 #1 才进入可滚动列表；滚到可见以确认展开生效（之前被截断掉）。
    await tester.scrollUntilVisible(find.text('任务 #1 - chapter_summary'), 300.0);
    expect(find.text('任务 #1 - chapter_summary'), findsOneWidget);
  });

  testWidgets('analysis jobs screen cancels a running job', (WidgetTester tester) async {
    final client = FakeNovelApiClient()..listJobsIncludeRunning = true;
    await tester.pumpWidget(
      MaterialApp(home: AnalysisJobsScreen(apiClient: client)),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('cancel-job-2')), findsOneWidget);
    await tester.tap(find.byKey(const Key('cancel-job-2')));
    await tester.pumpAndSettle();

    expect(client.cancelledJobId, 2);
  });

  testWidgets('analysis jobs polling refresh does not flash a loading spinner', (WidgetTester tester) async {
    final client = FakeNovelApiClient()..listJobsIncludeRunning = true;
    await tester.pumpWidget(
      MaterialApp(home: AnalysisJobsScreen(apiClient: client)),
    );
    await tester.pumpAndSettle();

    // First load shows the list, not a full-screen loading spinner.
    expect(find.text('共 2 个任务'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);

    // Advance the clock to fire the 2s background poll. The old design handed a
    // brand-new Future to a FutureBuilder on every tick, rebuilding into a
    // waiting state and flashing a spinner; the held-state design must keep the
    // list visible with no loading indicator.
    await tester.pump(const Duration(seconds: 2));
    expect(find.text('共 2 个任务'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);

    await tester.pumpAndSettle();
    expect(find.text('共 2 个任务'), findsOneWidget);
  });

  testWidgets('chapter list starts whole-book analysis and can cancel it', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(NovelAnalysisApp(apiClient: client));

    await tester.tap(find.text('书架'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Sample Novel'));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('analyze-whole-book-button')), findsOneWidget);
    expect(find.text('一键分析全书'), findsOneWidget);

    await tester.tap(find.byKey(const Key('analyze-whole-book-button')));
    await tester.pump(); // tap processed + startWholeBookAnalysis called
    await tester.pump(); // start completes, poll loop observes running job

    expect(client.startWholeBookNovelId, 1);
    expect(find.byKey(const Key('cancel-whole-book-button')), findsOneWidget);

    await tester.tap(find.byKey(const Key('cancel-whole-book-button')));
    await tester.pump(); // cancel call
    await tester.pump(); // cancel completes
    expect(client.cancelledJobId, 97);

    await tester.pump(const Duration(seconds: 3)); // poll loop observes cancelled status
    await tester.pump();
    expect(find.byKey(const Key('cancel-whole-book-button')), findsNothing);
    expect(find.text('一键分析全书'), findsOneWidget);
  });

  testWidgets('whole-book analysis keeps running far past the old poll cap', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(NovelAnalysisApp(apiClient: client));

    await tester.tap(find.text('书架'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Sample Novel'));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('analyze-whole-book-button')));
    await tester.pump();
    await tester.pump();

    expect(find.byKey(const Key('cancel-whole-book-button')), findsOneWidget);

    // Simulate many poll cycles, well beyond the old 360s cap, then assert
    // the UI never flips to a fake "timeout" state and stays running.
    for (int i = 0; i < 250; i++) {
      await tester.pump(const Duration(seconds: 3));
    }

    expect(find.byKey(const Key('cancel-whole-book-button')), findsOneWidget);
    expect(find.text('全书分析轮询超时'), findsNothing);
    expect(find.text('任务在后台运行，可切换页面，回来自动续看'), findsOneWidget);

    // Cancel so the infinite poll loop observes a terminal status and exits,
    // leaving no pending timers for the harness to complain about.
    await tester.tap(find.byKey(const Key('cancel-whole-book-button')));
    await tester.pump();
    await tester.pump();
    await tester.pump(const Duration(seconds: 3));
    await tester.pump();
    expect(find.byKey(const Key('cancel-whole-book-button')), findsNothing);
  });

  testWidgets('model settings screen shows cumulative usage stats', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(NovelAnalysisApp(apiClient: client));

    await tester.tap(find.text('模型设置'));
    await tester.pumpAndSettle();

    expect(find.text('累计调用统计'), findsOneWidget);
    expect(find.text('累计模型调用（成功缓存）: 3 次'), findsOneWidget);
    expect(find.text('累计模型调用（含失败尝试）: 4 次'), findsOneWidget);
    expect(find.text('本地兜底结果: 1 条'), findsOneWidget);
    expect(find.text('失败任务数: 2'), findsOneWidget);
    expect(find.text('缓存条目数: 5'), findsOneWidget);
    expect(find.textContaining('token 用量暂未采集'), findsOneWidget);
  });

  testWidgets('model settings screen loads and saves settings', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(NovelAnalysisApp(apiClient: client));

    await tester.tap(find.text('模型设置'));
    await tester.pumpAndSettle();

    expect(find.text('API Key 已设置: 是'), findsOneWidget);
    expect(find.widgetWithText(TextFormField, 'https://example.test/v1'), findsOneWidget);
    expect(find.widgetWithText(TextFormField, 'gpt-test'), findsOneWidget);

    await tester.enterText(find.byKey(const Key('settings-api-key-field')), 'sk-new');
    await tester.enterText(find.byKey(const Key('settings-base-url-field')), 'https://api.example/v1');
    await tester.enterText(find.byKey(const Key('settings-model-field')), 'gpt-4.1-mini');
    await tester.tap(find.byKey(const Key('settings-save-button')));
    await tester.pumpAndSettle();

    expect(client.savedApiKey, 'sk-new');
    expect(client.savedBaseUrl, 'https://api.example/v1');
    expect(client.savedModel, 'gpt-4.1-mini');
    expect(find.text('设置已保存'), findsOneWidget);
    expect(find.text('API Key 已设置: 是'), findsOneWidget);
    expect(find.text('sk-new'), findsNothing);
    await tester.enterText(find.byKey(const Key('settings-api-key-field')), 'sk-new');

    await tester.tap(find.byKey(const Key('settings-test-connection-button')));
    await tester.pumpAndSettle();

    expect(find.textContaining('Connection test succeeded.'), findsOneWidget);
    expect(find.textContaining('gpt-4.1-mini @ https://api.example/v1'), findsOneWidget);
  });

  testWidgets('bookshelf deletes a novel after confirmation', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(NovelAnalysisApp(apiClient: client));

    await tester.tap(find.text('书架'));
    await tester.pumpAndSettle();
    expect(find.text('Sample Novel'), findsOneWidget);

    await tester.tap(find.byKey(const Key('delete-novel-1')));
    await tester.pumpAndSettle();
    expect(find.text('删除小说?'), findsOneWidget);

    await tester.tap(find.widgetWithText(FilledButton, '删除'));
    await tester.pumpAndSettle();

    expect(client.deletedNovelId, 1);
    expect(find.text('Sample Novel'), findsNothing);
    expect(find.text('尚未导入小说'), findsOneWidget);
  });

  testWidgets('settings screen lists categories and starts extraction', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(MaterialApp(
      home: SettingsScreen(
        apiClient: client,
        novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('设定'), findsOneWidget);
    expect(find.text('一键抽取设定'), findsOneWidget);
    expect(find.textContaining('世界观规则'), findsOneWidget);
    await tester.tap(find.text('一键抽取设定'));
    await tester.pump();
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(client.startSettingsNovelId, 1);
  });

  testWidgets('timeline screen starts event extraction', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(MaterialApp(
      home: TimelineScreen(
        apiClient: client,
        novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('事件时间线'), findsOneWidget);
    expect(find.text('一键抽取时间线'), findsOneWidget);
    await tester.tap(find.text('一键抽取时间线'));
    await tester.pump();
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(client.startEventsNovelId, 1);
  });

  testWidgets('conflict detection screen shows review workflow', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(MaterialApp(
      home: ConflictDetectionScreen(
        apiClient: client,
        novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('设定冲突检测'), findsOneWidget);
    expect(find.text('检测设定冲突'), findsOneWidget);
    expect(find.textContaining('人工复核'), findsOneWidget);
    expect(find.text('待复核'), findsWidgets);
    await tester.tap(find.text('检测设定冲突'));
    await tester.pump();
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(client.startConflictsNovelId, 1);
  });

  // D1：时间线先按 era 分组、组内按 story_time_order 排序；无法判断时序的事件
  // 归入「时序不明」组并排末尾，页面标注「AI 推断时序，供参考」。
  testWidgets('timeline groups events by era and sorts by story time order', (WidgetTester tester) async {
    tester.view.physicalSize = const Size(800, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final client = FakeNovelApiClient()
      ..eventFactsOverride = [
        ExtractedFact(
          id: 30,
          novelId: 1,
          factType: 'event',
          content: '大闹天宫: 孙悟空打翻天庭',
          entities: ['孙悟空'],
          chapterId: 12,
          sourceQuote: '悟空打翻了八卦炉。',
          confidence: 'medium',
          status: 'pending_review',
          evidence: const [],
          extra: {'era': '五百年前', 'story_time_order': 2, 'chapter_order': 12, 'chapter_title': '第十二回 天庭', 'time_context': '五百年前', 'event_order': 1},
        ),
        ExtractedFact(
          id: 31,
          novelId: 1,
          factType: 'event',
          content: '拜师学艺: 孙悟空学成本领',
          entities: ['孙悟空'],
          chapterId: 5,
          sourceQuote: '悟空学艺归来。',
          confidence: 'medium',
          status: 'pending_review',
          evidence: const [],
          extra: {'era': '五百年前', 'story_time_order': 1, 'chapter_order': 5, 'chapter_title': '第五回 学艺', 'time_context': '灵台方寸山', 'event_order': 2},
        ),
        ExtractedFact(
          id: 32,
          novelId: 1,
          factType: 'event',
          content: '取经启程: 唐僧西行',
          entities: ['唐僧'],
          chapterId: 6,
          sourceQuote: '玄奘踏上西行路。',
          confidence: 'medium',
          status: 'pending_review',
          evidence: const [],
          extra: {'era': '取经路上', 'story_time_order': 3, 'chapter_order': 6, 'chapter_title': '第六回 出发', 'time_context': '贞观十三年', 'event_order': 3},
        ),
        ExtractedFact(
          id: 33,
          novelId: 1,
          factType: 'event',
          content: '混沌旧事: 时序不明',
          entities: ['混沌'],
          chapterId: 3,
          sourceQuote: '上古之事不可考。',
          confidence: 'low',
          status: 'pending_review',
          evidence: const [],
          extra: {'chapter_order': 3, 'chapter_title': '第三回 混沌', 'time_context': '远古', 'event_order': 4},
        ),
      ];
    await tester.pumpWidget(MaterialApp(
      home: TimelineScreen(
        apiClient: client,
        novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
      ),
    ));
    await tester.pumpAndSettle();

    // 分组头：先 era 组，最后是「时序不明」组（含 AI 推断标注）。
    expect(find.text('五百年前'), findsOneWidget);
    expect(find.text('取经路上'), findsOneWidget);
    expect(find.text('时序不明（AI 推断时序，供参考）'), findsOneWidget);
    // 页面顶部标注：时序为 AI 推断，仅供参考。
    expect(find.textContaining('AI 推断，仅供参考'), findsOneWidget);

    // 组内按 story_time_order 排序：拜师学艺(order 1) 在大闹天宫(order 2) 之前。
    final firstEvent = find.textContaining('拜师学艺');
    final secondEvent = find.textContaining('大闹天宫');
    expect(tester.getCenter(firstEvent).dy, lessThan(tester.getCenter(secondEvent).dy));
    // 时序不明组排在 era 组之后。
    final unknownGroup = find.text('时序不明（AI 推断时序，供参考）');
    expect(tester.getCenter(unknownGroup).dy, greaterThan(tester.getCenter(find.text('取经路上')).dy));
    // 事件保留章节定位信息。
    expect(find.text('章节：第12章 · 第十二回 天庭'), findsOneWidget);
  });

  // A2 回归：conflict 页 retry 使用块体 setState，回调不返回 Future，刷新不抛异常。
  testWidgets('conflict retry refresh does not leak a Future from setState', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(MaterialApp(
      home: ConflictDetectionScreen(
        apiClient: client,
        novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
      ),
    ));
    await tester.pumpAndSettle();
    // 列表已渲染（listFacts 返回 1 条 setting_conflict）。
    expect(find.textContaining('Li Qing'), findsWidgets);
    // 下拉触发 RefreshIndicator -> onRefresh -> _retry() -> setState 块体。
    await tester.fling(find.byType(ListView), const Offset(0, 300), 1000.0);
    await tester.pump();
    await tester.pumpAndSettle();
    expect(find.textContaining('Li Qing'), findsWidgets);
    // setState 回调不得返回 Future（debug 模式会报 FlutterError）。
    expect(tester.takeException(), isNull);
  });

  // A8 回归：导出报告页主按钮“保存 Markdown 到…” 调用 exportReport 并把 markdown 写入用户选择的路径；预览截断。
  // 用注入式 savePathPicker + 内存 fileWriter 避免真实磁盘 IO（fake-async 下 File IO 不上 pump，会挂起测试）。
  testWidgets('export report saves markdown to chosen file', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    String? savedPath;
    String? savedMarkdown;
    await tester.pumpWidget(
      MaterialApp(
        home: ExportReportScreen(
          apiClient: client,
          novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
          savePathPicker: (_) async => 'sample-novel-report.md',
          fileWriter: (path, markdown) async {
            savedPath = path;
            savedMarkdown = markdown;
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('export-report-button')));
    await tester.pump();
    await tester.pump();
    await tester.pump();

    expect(client.exportedNovelId, 1);
    expect(savedPath, 'sample-novel-report.md');
    expect(savedMarkdown, isNotNull);
    expect(savedMarkdown, contains('报告'));
    expect(find.byKey(const Key('markdown-export-result')), findsOneWidget);
  });

  // E4：人物/关系/时间线/设定/冲突各列表与图默认只读非 superseded 的事实。
  testWidgets('character list hides superseded characters', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..returnSupersededCharacter = true
      ..includeExtractionJobs = true;
    await tester.pumpWidget(
      MaterialApp(
        home: CharacterProfilesScreen(
          apiClient: client,
          novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
        ),
      ),
    );
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(find.text('共找到 1 个人物'), findsOneWidget);
    expect(find.text('Li Qing'), findsOneWidget);
    expect(find.text('Ghost'), findsNothing);
  });

  testWidgets('relationship graph hides superseded nodes and edges', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..graphIncludeSuperseded = true
      ..includeExtractionJobs = true;
    await tester.pumpWidget(
      MaterialApp(
        home: RelationshipGraphScreen(
          apiClient: client,
          novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
        ),
      ),
    );
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(find.text('2 个人物, 1 条关系'), findsOneWidget);
    expect(find.text('Ghost'), findsNothing);
  });

  testWidgets('timeline hides superseded events', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..eventFactsOverride = [
        ExtractedFact(
          id: 41,
          novelId: 1,
          factType: 'event',
          content: '大闹天宫: 孙悟空打翻天庭',
          entities: ['孙悟空'],
          chapterId: 1,
          sourceQuote: '悟空打翻了八卦炉。',
          confidence: 'medium',
          status: 'pending_review',
          evidence: const [],
          extra: {'chapter_order': 1, 'chapter_title': '第一章'},
        ),
        ExtractedFact(
          id: 42,
          novelId: 1,
          factType: 'event',
          content: '旧事件: 已取代',
          entities: ['孙悟空'],
          chapterId: 2,
          sourceQuote: '旧引文。',
          confidence: 'low',
          status: 'superseded',
          evidence: const [],
          extra: {'chapter_order': 2, 'chapter_title': '第二章'},
        ),
      ];
    await tester.pumpWidget(
      MaterialApp(
        home: TimelineScreen(
          apiClient: client,
          novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('大闹天宫'), findsOneWidget);
    expect(find.textContaining('旧事件'), findsNothing);
  });

  testWidgets('settings page hides superseded facts', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..settingsFactsOverride = [
        ExtractedFact(
          id: 51,
          novelId: 1,
          factType: 'world_rule',
          content: '灵气: 存在',
          entities: ['灵气'],
          sourceQuote: '天地有灵气。',
          confidence: 'medium',
          status: 'pending_review',
        ),
        ExtractedFact(
          id: 52,
          novelId: 1,
          factType: 'world_rule',
          content: '旧规则: 已取代',
          entities: ['旧规则'],
          sourceQuote: '旧引文。',
          confidence: 'low',
          status: 'superseded',
        ),
      ];
    await tester.pumpWidget(
      MaterialApp(
        home: SettingsScreen(
          apiClient: client,
          novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('灵气: 存在'), findsWidgets);
    expect(find.textContaining('旧规则'), findsNothing);
  });

  testWidgets('conflict page hides superseded conflicts', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..conflictFactsOverride = [
        ExtractedFact(
          id: 61,
          novelId: 1,
          factType: 'setting_conflict',
          content: '李青年龄矛盾',
          entities: ['李青'],
          sourceQuote: '十岁与二十岁。',
          confidence: 'medium',
          status: 'pending_review',
          extra: {
            'severity': 'high',
            'type': 'character_profile',
            'earlier_evidence': [
              {'source_quote': '十岁。', 'chapter_order': 1},
            ],
            'later_evidence': [
              {'source_quote': '二十岁。', 'chapter_order': 4},
            ],
          },
        ),
        ExtractedFact(
          id: 62,
          novelId: 1,
          factType: 'setting_conflict',
          content: '旧冲突: 已取代',
          entities: ['李青'],
          sourceQuote: '旧引文。',
          confidence: 'low',
          status: 'superseded',
        ),
      ];
    await tester.pumpWidget(
      MaterialApp(
        home: ConflictDetectionScreen(
          apiClient: client,
          novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('李青年龄矛盾'), findsOneWidget);
    expect(find.textContaining('旧冲突'), findsNothing);
  });

  // E3：任务页对 cached_partial 来源显示"部分成功"提示。
  testWidgets('analysis jobs show partial-success hint for cached_partial', (WidgetTester tester) async {
    final client = FakeNovelApiClient()
      ..listJobsOverride = [
        AnalysisJob(
          id: 7,
          novelId: 1,
          taskType: 'setting_extraction',
          status: 'completed',
          progress: 100,
          error: 'ReadTimeout',
          retryCount: 0,
          resultCacheKey: 'k7',
          requestJson: '{}',
          requestedModel: 'gpt-test',
          effectiveModel: 'gpt-test',
          cacheSource: 'cached_partial',
          modelError: 'ReadTimeout',
          providerCallAttempted: true,
          providerCallSucceeded: false,
          localFallback: false,
          createdAt: '2026-01-01',
          updatedAt: '2026-01-01',
        ),
      ];
    await tester.pumpWidget(MaterialApp(home: AnalysisJobsScreen(apiClient: client)));
    await tester.pumpAndSettle();

    expect(find.text('部分成功（有批次走了本地兜底，建议重跑）'), findsOneWidget);
    expect(find.textContaining('来源: cached_partial'), findsOneWidget);
  });

  // E3：ProvenancePanel 对 mixed 来源显示"部分成功"提示。
  testWidgets('provenance panel shows partial-success hint for mixed source', (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ProvenancePanel(
            status: 'partial',
            provenance: _testProvenance(source: 'mixed'),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('部分成功（有批次走了本地兜底，建议重跑）'), findsOneWidget);
  });

  // E5：关系图页"清除关系缓存"走 clearNovelCache(relationship_extraction)。
  testWidgets('relationship graph clears relationship cache', (WidgetTester tester) async {
    final client = FakeNovelApiClient()..includeExtractionJobs = true;
    await tester.pumpWidget(
      MaterialApp(
        home: RelationshipGraphScreen(
          apiClient: client,
          novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
        ),
      ),
    );
    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('clear-relationship-cache-button')));
    await tester.pump();
    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();

    expect(client.clearedCacheNovelId, 1);
    expect(client.clearedCacheTaskType, 'relationship_extraction');
  });

  // E5：设定页"重新抽取"带 force_refresh=true；"清除设定缓存"覆盖 setting_extraction。
  testWidgets('settings page re-extracts with force refresh and clears cache', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(MaterialApp(
      home: SettingsScreen(
        apiClient: client,
        novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
      ),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('re-extract-settings-button')));
    await tester.pump();
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(client.startSettingsNovelId, 1);
    expect(client.startSettingsForceRefresh, isTrue);

    await tester.tap(find.byKey(const Key('clear-setting-cache-button')));
    await tester.pump();
    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();
    expect(client.clearedCacheNovelId, 1);
    expect(client.clearedCacheTaskType, 'setting_extraction');
  });

  // E5：时间线页"重新抽取事件"带 force_refresh=true。
  testWidgets('timeline re-extracts events with force refresh', (WidgetTester tester) async {
    final client = FakeNovelApiClient();
    await tester.pumpWidget(MaterialApp(
      home: TimelineScreen(
        apiClient: client,
        novel: Novel(id: 1, title: 'Sample Novel', chapterCount: 3, chunkCount: 5, encoding: 'utf-8'),
      ),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('re-extract-events-button')));
    await tester.pump();
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(client.startEventsNovelId, 1);
    expect(client.startEventsForceRefresh, isTrue);
  });
}

ModelProvenance _testProvenance({String source = 'local_fallback', bool cacheHit = false}) {
  return ModelProvenance(
    taskType: 'test_task',
    modelUsed: 'gpt-test',
    source: source,
    cacheHit: cacheHit,
    localFallback: source.contains('fallback'),
    modelError: '',
    cacheKey: 'cache-key',
    providerCallAttempted: !cacheHit && !source.contains('fallback'),
    providerCallSucceeded: source == 'remote_model',
    jobId: 99,
  );
}

class FakeNovelApiClient implements NovelApiClient {
  String? importedTitle;
  String? importedText;
  String? savedApiKey;
  String? savedBaseUrl;
  String? savedModel;
  int? summarizedChapterId;
  bool? summaryForceRefresh;
  int? outlinedNovelId;
  bool? outlineForceRefresh;
  int? exportedNovelId;
  int? deletedNovelId;
  int? clearedCacheNovelId;
  String? clearedCacheTaskType;
  int? retriedJobId;
  int? ranJobId;
  bool ranNextJob = false;
  String? askedQuestion;
  bool? qaForceRefresh;
  bool includeSampleNovel = true;
  bool returnAttributeCharacter = false;
  bool returnAffiliationCharacter = false;
  bool returnDuplicateCandidates = false;
  bool returnSupersededCharacter = false;
  bool graphIncludeSuperseded = false;
  List<ExtractedFact>? settingsFactsOverride;
  List<ExtractedFact>? conflictFactsOverride;
  @override
  String get baseUrl => 'http://fake-backend';

  @override
  Future<Map<String, dynamic>> health() async {
    return {'status': 'ok', 'scope': 'mvp'};
  }

  @override
  Future<List<Novel>> listNovels() async {
    if (!includeSampleNovel) return <Novel>[];
    return [
      Novel(
        id: 1,
        title: 'Sample Novel',
        chapterCount: 3,
        chunkCount: 5,
        encoding: 'utf-8',
      ),
    ];
  }

  @override
  Future<List<ChapterSummary>> listChapters(int novelId) async {
    return [
      ChapterSummary(
        id: 10,
        order: 1,
        title: 'Opening Moves',
        charCount: 128,
      ),
    ];
  }

  @override
  Future<Chapter> getChapter(int chapterId) async {
    return Chapter(
      id: chapterId,
      novelId: 1,
      order: 1,
      title: 'Opening Moves',
      content: 'Li Qing arrives at Qingshi Town and meets Wang.',
    );
  }

  @override
  Future<ChapterSummaryResult> summarizeChapter(int chapterId, {bool forceRefresh = false}) async {
    summarizedChapterId = chapterId;
    summaryForceRefresh = forceRefresh;
    return ChapterSummaryResult(
      status: 'local_fallback',
      shortSummary: 'Li Qing reaches town and receives a warning.',
      keyEvents: ['Li Qing arrives at Qingshi Town'],
      cacheHit: false,
      jobId: 99,
      provenance: _testProvenance(),
    );
  }
  int? startSummaryChapterId;
  bool? startSummaryForceRefresh;

  @override
  Future<JobStartResult> startChapterSummary(int chapterId, {bool forceRefresh = false}) async {
    startSummaryChapterId = chapterId;
    startSummaryForceRefresh = forceRefresh;
    return JobStartResult(jobId: 98, status: 'queued', duplicated: false, effectiveModel: 'gpt-test');
  }

  @override
  Future<ModelConnectionTestResult> testConnection({
    required String apiKey,
    required String baseUrl,
    required String model,
  }) async {
    return ModelConnectionTestResult(
      ok: true,
      status: 'ok',
      message: 'Connection test succeeded.',
      model: model,
      baseUrl: baseUrl,
    );
  }

  int? startOutlineNovelId;
  bool? startOutlineForceRefresh;

  @override
  Future<JobStartResult> startOutline(int novelId, {bool forceRefresh = false}) async {
    startOutlineNovelId = novelId;
    startOutlineForceRefresh = forceRefresh;
    return JobStartResult(jobId: 99, status: 'queued', duplicated: false, effectiveModel: 'gpt-test');
  }

  int? startStageOutlineNovelId;
  bool? startStageOutlineForceRefresh;

  @override
  Future<JobStartResult> startStageOutline(int novelId, {bool forceRefresh = false}) async {
    startStageOutlineNovelId = novelId;
    startStageOutlineForceRefresh = forceRefresh;
    return JobStartResult(jobId: 96, status: 'queued', duplicated: false, effectiveModel: 'gpt-test');
  }

  @override
  Future<BookStageOutlineResult> generateStageOutline(int novelId, {bool forceRefresh = false}) async {
    return BookStageOutlineResult(
      status: 'ok',
      stages: const <BookStageOutlineStage>[],
      evidence: const <Map<String, dynamic>>[],
      cacheHit: false,
      jobId: 96,
      provenance: ModelProvenance(
        taskType: 'book_stage_outline',
        modelUsed: 'gpt-test',
        source: 'remote_model',
        cacheHit: false,
        localFallback: false,
        modelError: '',
        cacheKey: 'stage-key',
        providerCallAttempted: true,
        providerCallSucceeded: true,
        jobId: 96,
      ),
    );
  }

  int? startWholeBookNovelId;
  int? cancelledJobId;
  bool wholeBookCancelled = false;
  bool qaRunning = false;
  bool listJobsIncludeRunning = false;
  bool includeExtractionJobs = false;
  bool extractionJobRunning = false;
  List<AnalysisJob>? listJobsOverride;

  @override
  Future<JobStartResult> startWholeBookAnalysis(int novelId, {bool forceRefresh = false}) async {
    startWholeBookNovelId = novelId;
    return JobStartResult(jobId: 97, status: 'queued', duplicated: false, effectiveModel: 'gpt-test');
  }

  @override
  Future<AnalysisJob> cancelAnalysisJob(int jobId) async {
    cancelledJobId = jobId;
    if (jobId == 97) {
      wholeBookCancelled = true;
    }
    return AnalysisJob(
      id: jobId,
      novelId: 1,
      taskType: 'whole_book_analysis',
      status: 'cancelled',
      progress: 42,
      error: '',
      retryCount: 0,
      resultCacheKey: '',
      requestJson: '{"model":"gpt-test"}',
      requestedModel: 'gpt-test',
      effectiveModel: 'gpt-test',
      cacheSource: '',
      modelError: '',
      providerCallAttempted: false,
      providerCallSucceeded: false,
      localFallback: false,
      createdAt: '2026-01-01',
      updatedAt: '2026-01-01',
    );
  }

  @override
  Future<JobResultResponse> getJobResult(int jobId) async {
    if (jobId == 96) {
      return JobResultResponse(
        jobId: jobId,
        status: 'completed',
        provenance: {
          'task_type': 'book_stage_outline', 'model_used': 'gpt-test', 'source': 'remote_model',
          'cache_hit': false, 'local_fallback': false, 'model_error': null, 'cache_key': 'stage-key',
          'job_id': jobId, 'provider_call_attempted': true, 'provider_call_succeeded': true,
          'prompt_version': 'v1', 'schema_version': 'v1', 'input_hash': 'stage-hash',
        },
        result: {
          'status': 'ok',
          'task_type': 'book_stage_outline',
          'stages': [
            {
              'stage_index': 1, 'title': '闹天宫', 'chapter_start': 1, 'chapter_end': 2,
              'location': '花果山', 'characters': ['孙悟空'],
              'event': '孙悟空大闹天宫', 'resolution': '与天庭交战', 'outcome': '被压五行山',
            },
          ],
          'evidence': [
            {'chapter_order': 1, 'source_quote': '孙悟空打到凌霄宝殿外，众神不能敌。'},
          ],
          'source': 'remote_model', 'cache_hit': false, 'cache_key': 'stage-key', 'job_id': jobId,
        },
      );
    }
    if (jobId == 98) {
      return JobResultResponse(
        jobId: jobId,
        status: 'completed',
        provenance: {
          'task_type': 'chapter_summary', 'model_used': 'gpt-test', 'source': 'local_fallback',
          'cache_hit': false, 'local_fallback': true, 'model_error': null, 'cache_key': 'summary-key',
          'job_id': jobId, 'provider_call_attempted': false, 'provider_call_succeeded': false,
        },
        result: {
          'status': 'local_fallback',
          'task_type': 'chapter_summary',
          'short_summary': 'Li Qing reaches town and receives a warning.',
          'key_events': ['Li Qing arrives at Qingshi Town'],
        },
      );
    }    if (jobId == 101) {
      return JobResultResponse(
        jobId: jobId,
        status: 'completed',
        provenance: {
          'task_type': 'evidence_qa', 'model_used': 'gpt-test', 'source': 'cached_remote_model',
          'cache_hit': true, 'local_fallback': false, 'model_error': '', 'cache_key': 'qa-key',
          'job_id': jobId, 'provider_call_attempted': true, 'provider_call_succeeded': true,
        },
        result: {
          'status': 'ok',
          'answer': 'Li Qing arrived at Qingshi Town.',
          'evidence': [
            {'chapter_id': 1, 'chapter_order': 1, 'chapter_title': 'Opening Moves', 'source_quote': 'Li Qing arrives at Qingshi Town.', 'supports': 'Direct evidence'},
          ],
        },
      );
    }
    final chars = <CharacterItem>[
      if (returnAttributeCharacter)
        CharacterItem(
          name: '齐天大圣',
          roleType: 'protagonist',
          description: '花果山石猴。',
          aliases: ['孙悟空'],
          sourceChapters: [1, 5, 9],
          evidence: const [],
          confidence: 'high',
          reviewStatus: 'active',
          attributes: [
            CharacterAttribute(
              attribute: 'appearance',
              value: '毛脸雷公嘴',
              evidence: [
                CharacterEvidence(chapterId: 1, chapterOrder: 1, chapterTitle: '出世', sourceQuote: '石卵迸裂，化作一个石猴。'),
                CharacterEvidence(chapterId: 5, chapterOrder: 5, chapterTitle: '闹地府', sourceQuote: '尖嘴缩腮，毛脸雷公嘴。'),
              ],
            ),
            CharacterAttribute(
              attribute: 'personality',
              value: '桀骜不驯',
              evidence: [
                CharacterEvidence(chapterId: 5, chapterOrder: 5, chapterTitle: '闹地府', sourceQuote: '勾了生死簿，打出门去。'),
              ],
            ),
            CharacterAttribute(
              attribute: 'identity_background',
              value: '未提及',
              evidence: const [],
            ),
            CharacterAttribute(
              attribute: 'abilities',
              value: '七十二变',
              evidence: [
                CharacterEvidence(chapterId: 9, chapterOrder: 9, chapterTitle: '拜师', sourceQuote: '学会七十二般变化。'),
              ],
            ),
            CharacterAttribute(
              attribute: 'key_experiences',
              value: '大闹天宫',
              evidence: [
                CharacterEvidence(chapterId: 0, chapterOrder: 0, chapterTitle: '', sourceQuote: '来源未标注证据条。'),
              ],
            ),
            if (returnAffiliationCharacter)
              CharacterAttribute(
                attribute: 'affiliation',
                value: '妖族（第1-9章）→ 取经队伍（第10章起）',
                evidence: [
                  CharacterEvidence(chapterId: 3, chapterOrder: 3, chapterTitle: '结拜', sourceQuote: '与众妖结拜为兄弟。'),
                ],
              ),
          ],
        )
      else
        CharacterItem(
          name: 'Li Qing', aliases: [], sourceChapters: [1],
          evidence: [CharacterEvidence(chapterId: 1, chapterOrder: 1, chapterTitle: 'Opening Moves', sourceQuote: 'Li Qing arrives at Qingshi Town.')],
          confidence: 'low', reviewStatus: 'pending_review',
          roleType: 'supporting', description: 'A test character.',
        ),
      if (returnSupersededCharacter)
        CharacterItem(
          name: 'Ghost', aliases: [], sourceChapters: [2],
          evidence: [CharacterEvidence(chapterId: 2, chapterOrder: 2, chapterTitle: 'Later', sourceQuote: 'A ghost from an older run.')],
          confidence: 'low', reviewStatus: 'superseded',
          roleType: 'unknown', description: 'Older-run character.',
        ),
    ];
    return JobResultResponse(
      jobId: jobId, status: 'completed',
      result: {
        'status': 'local_fallback', 'title': 'Local chapter-order outline',
        'outline': 'Generated outline',
        'chapters': [
          {'order': 1, 'title': 'Opening Moves', 'summary': 'Li Qing enters town.', 'evidence': []},
        ],
        'characters': chars.map((c) => {
          'name': c.name, 'role_type': c.roleType, 'description': c.description, 'aliases': c.aliases, 'source_chapters': c.sourceChapters,
          'evidence': c.evidence.map((e) => {'chapter_id': e.chapterId, 'chapter_order': e.chapterOrder, 'chapter_title': e.chapterTitle, 'source_quote': e.sourceQuote}).toList(),
          'confidence': c.confidence, 'status': c.reviewStatus,
          'attributes': c.attributes.map((a) => {
            'attribute': a.attribute, 'value': a.value,
            'evidence': a.evidence.map((e) => {'chapter_id': e.chapterId, 'chapter_order': e.chapterOrder, 'chapter_title': e.chapterTitle, 'source_quote': e.sourceQuote}).toList(),
          }).toList(),
        }).toList(),
        'source': 'local_fallback', 'cache_hit': false, 'cache_key': 'test-key', 'job_id': jobId,
        if (returnDuplicateCandidates)
          'duplicate_candidates': [
            {'name_a': '沙僧', 'name_b': '沙和尚', 'reason': '别名交叉（疑似同一人物）'},
          ],
        'persisted_facts': returnAttributeCharacter ? 6 : 1,
        'provenance': {
          'task_type': 'character_extraction', 'model_used': 'gpt-test', 'source': 'local_fallback',
          'cache_hit': false, 'local_fallback': true, 'model_error': null,
          'prompt_version': 'v1', 'schema_version': 'v1', 'input_hash': 'abc', 'cache_key': 'test-key',
          'job_id': jobId, 'provider_call_attempted': false, 'provider_call_succeeded': false,
        },
      },
    );
  }

  int? startCharactersNovelId;
  bool? startCharactersForceRefresh;

  @override
  Future<JobStartResult> startCharacters({required int novelId, bool forceRefresh = false}) async {
    startCharactersNovelId = novelId;
    startCharactersForceRefresh = forceRefresh;
    return JobStartResult(jobId: 100, status: 'queued', duplicated: false, effectiveModel: 'gpt-test');
  }
  int? startRelationshipsNovelId;
  bool? startRelationshipsForceRefresh;

  @override
  Future<JobStartResult> startRelationships({required int novelId, bool forceRefresh = false}) async {
    startRelationshipsNovelId = novelId;
    startRelationshipsForceRefresh = forceRefresh;
    return JobStartResult(jobId: 120, status: 'queued', duplicated: false, effectiveModel: 'gpt-test');
  }

  int? startSettingsNovelId;
  bool? startSettingsForceRefresh;
  @override
  Future<JobStartResult> startSettings({required int novelId, bool forceRefresh = false}) async {
    startSettingsNovelId = novelId;
    startSettingsForceRefresh = forceRefresh;
    return JobStartResult(jobId: 130, status: 'queued', duplicated: false, effectiveModel: 'gpt-test');
  }

  int? startEventsNovelId;
  bool? startEventsForceRefresh;
  List<ExtractedFact>? eventFactsOverride;
  @override
  Future<JobStartResult> startEvents({required int novelId, bool forceRefresh = false}) async {
    startEventsNovelId = novelId;
    startEventsForceRefresh = forceRefresh;
    return JobStartResult(jobId: 131, status: 'queued', duplicated: false, effectiveModel: 'gpt-test');
  }

  int? startConflictsNovelId;
  bool? startConflictsForceRefresh;
  @override
  Future<JobStartResult> startConflicts({required int novelId, bool forceRefresh = false}) async {
    startConflictsNovelId = novelId;
    startConflictsForceRefresh = forceRefresh;
    return JobStartResult(jobId: 132, status: 'queued', duplicated: false, effectiveModel: 'gpt-test');
  }

  @override
  Future<RelationshipGraphResult> fetchRelationshipGraph({required int novelId}) async {
    final nodes = <RelationshipNode>[
      RelationshipNode(name: 'Li Qing', confidence: 'high', status: 'pending_review', factId: 1),
      RelationshipNode(name: 'Wang', confidence: 'medium', status: 'pending_review', factId: 2),
      if (graphIncludeSuperseded)
        RelationshipNode(name: 'Ghost', confidence: 'low', status: 'superseded', factId: 9),
    ];
    final edges = <RelationshipEdge>[
      RelationshipEdge(
        id: 3,
        source: 'Li Qing',
        target: 'Wang',
        relationType: 'friend',
        relationLabel: '师弟',
        attitude: 'friendly',
        evolution: [
          RelationshipEvolution(chapterOrder: 1, relationLabel: '同门', event: '入门相识'),
        ],
        description: 'Travel companions.',
        confidence: 'high',
        status: 'pending_review',
        sourceQuote: 'Li Qing met Wang in town.',
        chapterTitle: 'Opening Moves',
        chapterId: 1,
        chapterOrder: 1,
      ),
      if (graphIncludeSuperseded)
        RelationshipEdge(
          id: 9,
          source: 'Ghost',
          target: 'Li Qing',
          relationType: 'other',
          description: 'An edge from an older run.',
          confidence: 'low',
          status: 'superseded',
          sourceQuote: 'Old quote.',
          chapterTitle: 'Later',
        ),
    ];
    return RelationshipGraphResult(novelId: novelId, nodes: nodes, edges: edges);
  }


  @override
  Future<BookOutlineResult> generateOutline(int novelId, {bool forceRefresh = false}) async {
    outlinedNovelId = novelId;
    outlineForceRefresh = forceRefresh;
    return BookOutlineResult(
      status: 'local_fallback',
      title: 'Local chapter-order outline',
      chapters: [
        BookOutlineChapter(order: 1, title: 'Opening Moves', brief: 'Li Qing enters town.', isValid: true),
      ],
      cacheHit: false,
      jobId: 101,
      provenance: _testProvenance(),
      modelError: '',
    );
  }

  @override
  Future<ImportTxtResult> importTxt({required String title, required String text}) async {
    importedTitle = title;
    importedText = text;
    return ImportTxtResult(
      id: 2,
      title: title,
      imported: true,
      chapterCount: 2,
      chunkCount: 4,
      encoding: 'utf-8',
    );
  }

  @override
  Future<ImportTxtResult> importTxtFile({required String title, required String filename, required List<int> bytes}) async {
    importedTitle = title;
    return ImportTxtResult(
      id: 2,
      title: title,
      imported: true,
      chapterCount: 2,
      chunkCount: 4,
      encoding: 'utf-8',
    );
  }
  @override
  Future<UsageStats> fetchUsageStats() async {
    return UsageStats(
      cacheEntries: 5,
      modelCallsAttempted: 4,
      modelCallsSucceeded: 3,
      localFallbackResults: 1,
      failedJobs: 2,
      tokenStatsAvailable: false,
    );
  }

  @override
  Future<ModelSettings> getModelSettings() async {
    return ModelSettings(apiKeySet: true, baseUrl: 'https://example.test/v1', model: 'gpt-test');
  }

  @override
  Future<void> saveModelSettings({required String apiKey, required String baseUrl, required String model}) async {
    savedApiKey = apiKey;
    savedBaseUrl = baseUrl;
    savedModel = model;
  }

  @override
  Future<DeleteNovelResult> deleteNovel(int novelId) async {
    deletedNovelId = novelId;
    includeSampleNovel = false;
    return DeleteNovelResult(
      deleted: true,
      novelId: novelId,
      title: 'Sample Novel',
      deletedCacheEntries: 1,
    );
  }

  @override
  Future<ClearCacheResult> clearNovelCache(int novelId, {String? taskType}) async {
    clearedCacheNovelId = novelId;
    clearedCacheTaskType = taskType;
    return ClearCacheResult(
      cleared: true,
      novelId: novelId,
      title: 'Sample Novel',
      taskType: taskType ?? 'all',
      deletedCacheEntries: 1,
    );
  }

  @override
  Future<Map<String, dynamic>> exportMarkdown(int novelId) async {
    exportedNovelId = novelId;
    return {
      'filename': 'sample-novel.md',
      'content_type': 'text/markdown',
      'markdown': '# Sample Novel\n\n## Opening Moves',
    };
  }

  @override
  Future<Map<String, dynamic>> exportReport(int novelId, {bool includeChapters = true}) async {
    exportedNovelId = novelId;
    return {
      'filename': 'sample-novel-report.md',
      'content_type': 'text/markdown',
      'markdown': '# Sample Novel 报告\n\n## 全书大纲',
    };
  }

  @override
  Future<JobStartResult> startQa({
    required int novelId,
    required String question,
    String? model,
    bool forceRefresh = false,
  }) async {
    askedQuestion = question;
    qaForceRefresh = forceRefresh;
    return JobStartResult(jobId: 101, status: 'queued', duplicated: false, effectiveModel: 'gpt-test');
  }

  @override
  Future<QaResult> askQuestion({
    required int novelId,
    required String question,
    String? model,
    bool forceRefresh = false,
  }) async {
    askedQuestion = question;
    qaForceRefresh = forceRefresh;
    return QaResult(
      status: 'ok',
      answer: 'Li Qing arrived at Qingshi Town.',
      evidence: [
        QaEvidence(
          chapterId: 1,
          chapterOrder: 1,
          chapterTitle: 'Opening Moves',
          quote: 'Li Qing arrives at Qingshi Town.',
          supports: 'Direct evidence',
        ),
      ],
      reasoning: '',
      uncertainty: '',
      needsMoreContext: false,
      cacheHit: false,
      provenance: _testProvenance(source: 'remote_model'),
    );
  }

  @override
  Future<CharacterListResult> extractCharacters({
    required int novelId,
    String? model,
    bool forceRefresh = false,
  }) async {
    if (returnAttributeCharacter) {
      return CharacterListResult(
        status: 'remote_model',
        characters: [
          CharacterItem(
            name: '齐天大圣',
            roleType: 'protagonist',
            description: '花果山石猴。',
            aliases: ['孙悟空'],
            sourceChapters: [1, 5, 9],
            evidence: const [],
            confidence: 'high',
            reviewStatus: 'active',
            attributes: [
              CharacterAttribute(
                attribute: 'appearance',
                value: '毛脸雷公嘴',
                evidence: [
                  CharacterEvidence(chapterId: 1, chapterOrder: 1, chapterTitle: '出世', sourceQuote: '石卵迸裂，化作一个石猴。'),
                  CharacterEvidence(chapterId: 5, chapterOrder: 5, chapterTitle: '闹地府', sourceQuote: '尖嘴缩腮，毛脸雷公嘴。'),
                ],
              ),
              CharacterAttribute(
                attribute: 'personality',
                value: '桀骜不驯',
                evidence: [
                  CharacterEvidence(chapterId: 5, chapterOrder: 5, chapterTitle: '闹地府', sourceQuote: '勾了生死簿，打出门去。'),
                ],
              ),
              CharacterAttribute(
                attribute: 'identity_background',
                value: '未提及',
                evidence: const [],
              ),
              CharacterAttribute(
                attribute: 'abilities',
                value: '七十二变',
                evidence: [
                  CharacterEvidence(chapterId: 9, chapterOrder: 9, chapterTitle: '拜师', sourceQuote: '学会七十二般变化。'),
                ],
              ),
              CharacterAttribute(
                attribute: 'key_experiences',
                value: '大闹天宫',
                evidence: [
                  CharacterEvidence(chapterId: 0, chapterOrder: 0, chapterTitle: '', sourceQuote: '来源未标注证据条。'),
                ],
              ),
            ],
          ),
        ],
        cacheHit: false,
        persistedFacts: 5,
        provenance: _testProvenance(source: 'remote_model'),
      );
    }
    return CharacterListResult(
      status: 'local_fallback',
      characters: [
        CharacterItem(
          name: 'Li Qing',
          roleType: 'supporting',
          description: 'Arrives at Qingshi Town.',
          aliases: [],
          sourceChapters: [1],
          evidence: [
            CharacterEvidence(
              chapterId: 1,
              chapterOrder: 1,
              chapterTitle: 'Opening Moves',
              sourceQuote: 'Li Qing arrives at Qingshi Town.',
            ),
          ],
          confidence: 'low',
          reviewStatus: 'pending_review',
        ),
      ],
      cacheHit: false,
      persistedFacts: 1,
      provenance: _testProvenance(),
    );
  }

  @override
  Future<List<ExtractedFact>> listFacts({required int novelId, String? factType, String? status}) async {
    if (factType == 'event' && eventFactsOverride != null) {
      return eventFactsOverride!;
    }
    if (factType == 'setting_conflict' && conflictFactsOverride != null) {
      return conflictFactsOverride!;
    }
    if (settingsFactsOverride != null && factType != 'event' && factType != 'setting_conflict') {
      return settingsFactsOverride!
          .where((fact) => factType == null || fact.factType == factType)
          .toList();
    }
    return [
      ExtractedFact(
        id: 1,
        novelId: novelId,
        factType: factType ?? 'character_profile',
        content: 'Li Qing: unknown',
        entities: ['Li Qing'],
        chapterId: 1,
        sourceQuote: 'Li Qing arrives at Qingshi Town.',
        confidence: 'low',
        status: status ?? 'pending_review',
      ),
    ];
  }

  @override
  Future<ReviewUpdateResult> updateReviewStatus({
    required String recordType,
    required int recordId,
    required String status,
    String note = '',
  }) async {
    return ReviewUpdateResult(
      fact: ExtractedFact(
        id: recordId,
        novelId: 1,
        factType: 'character_profile',
        content: 'Li Qing: unknown',
        entities: ['Li Qing'],
        sourceQuote: 'Li Qing arrives at Qingshi Town.',
        confidence: 'low',
        status: status,
      ),
      reviewActions: [
        ReviewAction(
          id: 1,
          recordType: recordType,
          recordId: recordId,
          fromStatus: 'pending_review',
          toStatus: status,
          note: note,
        ),
      ],
    );
  }

  @override
  Future<List<AnalysisJob>> listAnalysisJobs({int? novelId}) async {
    if (listJobsOverride != null) return listJobsOverride!;
    return [
      if (listJobsIncludeRunning)
        AnalysisJob(
          id: 2,
          novelId: novelId ?? 1,
          taskType: 'whole_book_analysis',
          status: 'running',
          progress: 42,
          error: '',
          retryCount: 0,
          resultCacheKey: '',
          requestJson: '{"model":"gpt-test"}',
          requestedModel: 'gpt-test',
          effectiveModel: 'gpt-test',
          cacheSource: '',
          modelError: '',
          providerCallAttempted: true,
          providerCallSucceeded: true,
          localFallback: false,
          createdAt: '2026-01-01',
          updatedAt: '2026-01-01',
        ),
      AnalysisJob(
        id: 1,
        novelId: novelId ?? 1,
        taskType: 'chapter_summary',
        status: 'failed',
        progress: 100,
        error: 'temporary model error',
        retryCount: 0,
        resultCacheKey: 'cache-key-1',
        requestJson: '{"model":"gpt-test"}',
        requestedModel: 'gpt-test',
        effectiveModel: 'gpt-test',
        cacheSource: 'cached_local_fallback',
        modelError: 'temporary model error',
        providerCallAttempted: true,
        providerCallSucceeded: false,
        localFallback: true,
        createdAt: '2026-01-01',
        updatedAt: '2026-01-01',
      ),
      if (includeExtractionJobs) ...[
        AnalysisJob(
          id: 100,
          novelId: novelId ?? 1,
          taskType: 'character_extraction',
          status: extractionJobRunning ? 'running' : 'completed',
          progress: 100,
          error: '',
          retryCount: 0,
          resultCacheKey: 'character-key',
          requestJson: '{"model":"gpt-test"}',
          requestedModel: 'gpt-test',
          effectiveModel: 'gpt-test',
          cacheSource: 'cached_remote_model',
          modelError: '',
          providerCallAttempted: true,
          providerCallSucceeded: true,
          localFallback: false,
          createdAt: '2026-01-01',
          updatedAt: '2026-01-01',
        ),
        AnalysisJob(
          id: 120,
          novelId: novelId ?? 1,
          taskType: 'relationship_extraction',
          status: extractionJobRunning ? 'running' : 'completed',
          progress: 100,
          error: '',
          retryCount: 0,
          resultCacheKey: 'relationship-key',
          requestJson: '{"model":"gpt-test"}',
          requestedModel: 'gpt-test',
          effectiveModel: 'gpt-test',
          cacheSource: 'cached_remote_model',
          modelError: '',
          providerCallAttempted: true,
          providerCallSucceeded: true,
          localFallback: false,
          createdAt: '2026-01-01',
          updatedAt: '2026-01-01',
        ),
      ],
    ];
  }

  @override
  Future<AnalysisJob> getAnalysisJob(int jobId) async {
    if (jobId == 100) {
      return AnalysisJob(
        id: jobId,
        novelId: 1,
        taskType: 'character_extraction',
        status: extractionJobRunning ? 'running' : 'completed',
        progress: extractionJobRunning ? 40 : 100,
        error: '',
        retryCount: 0,
        resultCacheKey: 'character-key',
        requestJson: '{"model":"gpt-test"}',
        requestedModel: 'gpt-test',
        effectiveModel: 'gpt-test',
        cacheSource: '',
        modelError: '',
        providerCallAttempted: true,
        providerCallSucceeded: true,
        localFallback: false,
        createdAt: '2026-01-01',
        updatedAt: '2026-01-01',
      );
    }
    if (qaRunning && jobId == 101) {
      return AnalysisJob(
        id: jobId,
        novelId: 1,
        taskType: 'evidence_qa',
        status: 'running',
        progress: 10,
        error: '',
        retryCount: 0,
        resultCacheKey: '',
        requestJson: '{"model":"gpt-test"}',
        requestedModel: 'gpt-test',
        effectiveModel: 'gpt-test',
        cacheSource: '',
        modelError: '',
        providerCallAttempted: true,
        providerCallSucceeded: true,
        localFallback: false,
        createdAt: '2026-01-01',
        updatedAt: '2026-01-01',
      );
    }
    if (jobId == 97) {
      return AnalysisJob(
        id: jobId,
        novelId: 1,
        taskType: 'whole_book_analysis',
        status: wholeBookCancelled ? 'cancelled' : 'running',
        progress: 42,
        error: '',
        retryCount: 0,
        resultCacheKey: '',
        requestJson: '{"model":"gpt-test"}',
        requestedModel: 'gpt-test',
        effectiveModel: 'gpt-test',
        cacheSource: '',
        modelError: '',
        providerCallAttempted: true,
        providerCallSucceeded: true,
        localFallback: false,
        createdAt: '2026-01-01',
        updatedAt: '2026-01-01',
      );
    }
    return AnalysisJob(
      id: jobId,
      novelId: 1,
      taskType: 'chapter_summary',
      status: 'completed',
      progress: 100,
      error: '',
      retryCount: 0,
      resultCacheKey: 'cache-key',
      requestJson: '{"model":"gpt-test"}',
      requestedModel: 'gpt-test',
      effectiveModel: 'gpt-test',
      cacheSource: 'cached_remote_model',
      modelError: '',
      providerCallAttempted: true,
      providerCallSucceeded: true,
      localFallback: false,
      createdAt: '2026-01-01',
      updatedAt: '2026-01-01',
    );
  }

  @override
  Future<AnalysisJob> retryAnalysisJob(int jobId) async {
    retriedJobId = jobId;
    return AnalysisJob(
      id: jobId,
      novelId: 1,
      taskType: 'chapter_summary',
      status: 'queued',
      progress: 0,
      error: '',
      retryCount: 1,
      resultCacheKey: '',
      requestJson: '{"model":"gpt-test"}',
      requestedModel: 'gpt-test',
      effectiveModel: 'gpt-test',
      cacheSource: '',
      modelError: '',
      providerCallAttempted: false,
      providerCallSucceeded: false,
      localFallback: false,
      createdAt: '2026-01-01',
      updatedAt: '2026-01-01',
    );
  }

  @override
  Future<Map<String, dynamic>> runAnalysisJob(int jobId) async {
    ranJobId = jobId;
    return {
      'status': 'local_fallback',
      'job_id': jobId,
      'cache_hit': false,
    };
  }

  @override
  Future<Map<String, dynamic>> runNextAnalysisJob() async {
    ranNextJob = true;
    return {
      'status': 'local_fallback',
      'job_id': 1,
      'cache_hit': false,
    };
  }
}
