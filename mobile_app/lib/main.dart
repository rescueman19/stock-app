// 급락주 스크리너 - 안드로이드 앱
// GitHub가 매일 만들어 두는 결과 파일(data/results.json)을 받아서 보여줌
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/material.dart';

// 앱을 만들 때 GitHub가 자동으로 넣어주는 결과 파일 주소
const String dataUrl = String.fromEnvironment('DATA_URL');

const Color kNavy = Color(0xFF1F4E79);
const Color kRed = Color(0xFFC62828);
const Color kGreen = Color(0xFF2E7D32);
const Color kGrey = Color(0xFF6B7280);

void main() => runApp(const ScreenerApp());

class ScreenerApp extends StatelessWidget {
  const ScreenerApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '급락주 스크리너',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(useMaterial3: true, colorSchemeSeed: kNavy),
      home: const HomePage(),
    );
  }
}

// ───────────── 공통 도우미 ─────────────
String comma(num v, [int digits = 0]) {
  final s = v.toStringAsFixed(digits);
  final parts = s.split('.');
  final head = parts[0].replaceAllMapped(RegExp(r'(\d)(?=(\d{3})+$)'), (m) => '${m[1]},');
  return parts.length > 1 ? '$head.${parts[1]}' : head;
}

double? toD(dynamic v) => v is num ? v.toDouble() : null;

String fmtPrice(double? v, String market) {
  if (v == null) return '-';
  return market == 'KR' ? '${comma(v)}원' : '\$${comma(v, 2)}';
}

Future<Map<String, dynamic>> loadData() async {
  if (dataUrl.isEmpty) {
    throw '결과 파일 주소가 없습니다. GitHub에서 앱을 다시 만들어 주세요.';
  }
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 15);
  try {
    final uri = Uri.parse('$dataUrl?t=${DateTime.now().millisecondsSinceEpoch}');
    final req = await client.getUrl(uri);
    final res = await req.close();
    final body = await res.transform(utf8.decoder).join();
    if (res.statusCode != 200) {
      throw '결과 파일을 찾지 못했습니다 (응답 ${res.statusCode}).\n저장소가 공개(Public)인지 확인해 주세요.';
    }
    return jsonDecode(body) as Map<String, dynamic>;
  } finally {
    client.close();
  }
}

// ───────────── 첫 화면 ─────────────
class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  Map<String, dynamic>? data;
  String? error;
  bool loading = true;

  @override
  void initState() {
    super.initState();
    refresh();
  }

  Future<void> refresh() async {
    setState(() {
      loading = data == null;
      error = null;
    });
    try {
      final d = await loadData();
      if (!mounted) return;
      setState(() {
        data = d;
        loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      final msg = e is SocketException || e is HandshakeException
          ? '인터넷 연결을 확인해 주세요.'
          : e.toString();
      setState(() {
        error = msg;
        loading = false;
      });
      if (data != null) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final markets = (data?['markets'] as Map<String, dynamic>?) ?? {};
    final updated = data?['updated_at'] as String?;
    Widget body;
    if (loading) {
      body = const Center(child: CircularProgressIndicator());
    } else if (data == null) {
      body = MessageView(message: error ?? '불러오지 못했습니다.', onRetry: refresh);
    } else {
      body = TabBarView(children: [
        MarketView(market: 'KR', data: markets['KR'] as Map<String, dynamic>?, updatedAt: updated, onRefresh: refresh),
        MarketView(market: 'US', data: markets['US'] as Map<String, dynamic>?, updatedAt: updated, onRefresh: refresh),
      ]);
    }
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('급락주 스크리너', style: TextStyle(fontWeight: FontWeight.w700)),
          actions: [
            IconButton(onPressed: refresh, icon: const Icon(Icons.refresh), tooltip: '새로고침'),
          ],
          bottom: const TabBar(tabs: [Tab(text: '국내 코스피'), Tab(text: '미국 나스닥')]),
        ),
        body: body,
      ),
    );
  }
}

class MessageView extends StatelessWidget {
  final String message;
  final Future<void> Function() onRetry;
  const MessageView({super.key, required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.cloud_off, size: 48, color: kGrey),
          const SizedBox(height: 16),
          Text(message, textAlign: TextAlign.center, style: const TextStyle(fontSize: 15, height: 1.5)),
          const SizedBox(height: 20),
          FilledButton(onPressed: onRetry, child: const Text('다시 불러오기')),
        ]),
      ),
    );
  }
}

// ───────────── 시장별 목록 ─────────────
class MarketView extends StatelessWidget {
  final String market;
  final Map<String, dynamic>? data;
  final String? updatedAt;
  final Future<void> Function() onRefresh;
  const MarketView({super.key, required this.market, required this.data, required this.updatedAt, required this.onRefresh});

  @override
  Widget build(BuildContext context) {
    if (data == null) {
      return RefreshIndicator(
        onRefresh: onRefresh,
        child: ListView(physics: const AlwaysScrollableScrollPhysics(), children: const [
          SizedBox(height: 140),
          Padding(
            padding: EdgeInsets.symmetric(horizontal: 32),
            child: Text('아직 결과가 없습니다.\n\nGitHub의 Actions 화면에서\n"매일 급락주 찾기"를 한 번 실행해 주세요.',
                textAlign: TextAlign.center, style: TextStyle(fontSize: 15, height: 1.5)),
          ),
        ]),
      );
    }
    final d = data!;
    final hits = ((d['hits'] as List?) ?? []).cast<Map<String, dynamic>>();
    final fx = toD(d['fx']);
    final ddPct = toD(d['drawdown_pct']) ?? 30;
    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 32),
        itemCount: hits.length + 2,
        itemBuilder: (context, i) {
          if (i == 0) return SummaryCard(data: d, updatedAt: updatedAt);
          if (i == hits.length + 1) {
            return Padding(
              padding: const EdgeInsets.only(top: 16),
              child: Text(
                hits.isEmpty
                    ? '지금은 조건에 맞는 종목이 없습니다.'
                    : '매수 후보를 추리는 참고용입니다. 투자 판단은 본인 책임입니다.',
                textAlign: TextAlign.center,
                style: const TextStyle(color: kGrey, fontSize: 12),
              ),
            );
          }
          return StockCard(item: hits[i - 1], market: market, fx: fx, ddPct: ddPct);
        },
      ),
    );
  }
}

class SummaryCard extends StatelessWidget {
  final Map<String, dynamic> data;
  final String? updatedAt;
  const SummaryCard({super.key, required this.data, required this.updatedAt});

  @override
  Widget build(BuildContext context) {
    final err = data['error'] as String?;
    final newCount = data['new_count'] ?? 0;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(color: kNavy, borderRadius: BorderRadius.circular(14)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(data['label'] ?? '', style: const TextStyle(color: Colors.white70, fontSize: 13)),
        const SizedBox(height: 6),
        Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
          Text('${data['count'] ?? 0}', style: const TextStyle(color: Colors.white, fontSize: 34, fontWeight: FontWeight.w800, height: 1)),
          const SizedBox(width: 6),
          const Padding(
            padding: EdgeInsets.only(bottom: 3),
            child: Text('종목이 조건에 해당', style: TextStyle(color: Colors.white, fontSize: 15)),
          ),
          const Spacer(),
          if (newCount > 0)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(20)),
              child: Text('새로 $newCount', style: const TextStyle(color: kRed, fontWeight: FontWeight.w700)),
            ),
        ]),
        const SizedBox(height: 10),
        Text('조건: ${data['condition'] ?? ''}', style: const TextStyle(color: Colors.white, fontSize: 13)),
        const SizedBox(height: 2),
        Text('주가 기준일 ${data['data_date'] ?? '-'} · 갱신 ${updatedAt ?? '-'}',
            style: const TextStyle(color: Colors.white70, fontSize: 12)),
        if (err != null) ...[
          const SizedBox(height: 8),
          Text(err, style: const TextStyle(color: Color(0xFFFFCDD2), fontSize: 12)),
        ],
      ]),
    );
  }
}

class StockCard extends StatelessWidget {
  final Map<String, dynamic> item;
  final String market;
  final double? fx;
  final double ddPct;
  const StockCard({super.key, required this.item, required this.market, required this.fx, required this.ddPct});

  @override
  Widget build(BuildContext context) {
    final dd = toD(item['dd']) ?? 0;
    final isNew = item['new'] == true;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      elevation: 0,
      color: const Color(0xFFF4F6F9),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => DetailPage(item: item, market: market, fx: fx, ddPct: ddPct),
        )),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(children: [
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Flexible(
                    child: Text(item['n'] ?? '', overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                  ),
                  if (isNew) ...[
                    const SizedBox(width: 6),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                      decoration: BoxDecoration(color: kRed, borderRadius: BorderRadius.circular(4)),
                      child: const Text('NEW', style: TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.w700)),
                    ),
                  ],
                ]),
                const SizedBox(height: 3),
                Text('${item['t']} · 현재 ${fmtPrice(toD(item['p']), market)}',
                    style: const TextStyle(color: kGrey, fontSize: 13)),
                const SizedBox(height: 3),
                Text('최고점 ${item['pd']} · ${item['st']}일째 조건 유지',
                    style: const TextStyle(color: kGrey, fontSize: 12)),
              ]),
            ),
            const SizedBox(width: 12),
            Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
              Text('${dd.toStringAsFixed(1)}%',
                  style: const TextStyle(color: kRed, fontSize: 22, fontWeight: FontWeight.w800)),
              const Text('최고점 대비', style: TextStyle(color: kGrey, fontSize: 11)),
            ]),
          ]),
        ),
      ),
    );
  }
}

// ───────────── 종목 상세 ─────────────
class DetailPage extends StatelessWidget {
  final Map<String, dynamic> item;
  final String market;
  final double? fx;
  final double ddPct;
  const DetailPage({super.key, required this.item, required this.market, required this.fx, required this.ddPct});

  @override
  Widget build(BuildContext context) {
    final prices = ((item['h'] as List?) ?? []).map((v) => toD(v)).whereType<double>().toList();
    final price = toD(item['p']);
    final peak = toD(item['pk']) ?? 0;
    final dd = toD(item['dd']) ?? 0;
    final rel = toD(item['rel']);
    final rsiV = toD(item['rsi']);

    String relText = '-';
    if (rel != null) {
      relText = rel < 0
          ? '시장보다 ${(-rel).toStringAsFixed(1)}%p 더 하락'
          : '시장보다 ${rel.toStringAsFixed(1)}%p 덜 하락';
    }
    String rsiText = '-';
    if (rsiV != null) {
      rsiText = '${rsiV.toStringAsFixed(0)}  (${rsiV <= 30 ? '많이 팔린 상태' : rsiV >= 70 ? '많이 오른 상태' : '보통'})';
    }

    return Scaffold(
      appBar: AppBar(title: Text(item['n'] ?? '')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Text('${item['t']}', style: const TextStyle(color: kGrey)),
        const SizedBox(height: 4),
        Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
          Text(fmtPrice(price, market), style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w800)),
          const SizedBox(width: 10),
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Text('${dd.toStringAsFixed(1)}%',
                style: const TextStyle(color: kRed, fontSize: 18, fontWeight: FontWeight.w700)),
          ),
        ]),
        if (market == 'US' && fx != null && price != null)
          Text('약 ${comma(price * fx!)}원', style: const TextStyle(color: kGrey)),
        const SizedBox(height: 20),
        SizedBox(
          height: 220,
          child: CustomPaint(
            size: Size.infinite,
            painter: PriceChartPainter(prices, peak, peak * (1 - ddPct / 100)),
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
            Text('${item['hs'] ?? ''}', style: const TextStyle(color: kGrey, fontSize: 11)),
            Text('${item['he'] ?? ''}', style: const TextStyle(color: kGrey, fontSize: 11)),
          ]),
        ),
        const SizedBox(height: 8),
        Wrap(spacing: 14, runSpacing: 4, children: [
          legend(kNavy, '최근 1년 주가', false),
          legend(kGreen, '기준 최고점', true),
          legend(kRed, '-${ddPct.toStringAsFixed(0)}% 선', true),
        ]),
        const SizedBox(height: 20),
        info('최고점', '${fmtPrice(peak, market)}  (${item['pd']})'),
        info('조건 유지', '${item['st']}거래일째'),
        info('시장 대비', relText, '같은 기간 시장 전체(지수)와 비교한 값'),
        info('RSI', rsiText, '30 이하면 단기간에 많이 팔린 상태'),
        info('시가총액', '${item['cap'] ?? '-'}'),
        const SizedBox(height: 16),
        const Text('떨어진 이유가 일시적인지, 회사 실적이 나빠져서인지 꼭 확인한 뒤 판단하세요.',
            style: TextStyle(color: kGrey, fontSize: 12, height: 1.5)),
      ]),
    );
  }

  Widget legend(Color c, String label, bool dashed) {
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Container(width: 16, height: dashed ? 1.5 : 2.5, color: c),
      const SizedBox(width: 4),
      Text(label, style: const TextStyle(fontSize: 12, color: kGrey)),
    ]);
  }

  Widget info(String label, String value, [String? hint]) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 12),
      decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: Color(0xFFE5E7EB)))),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 80, child: Text(label, style: const TextStyle(color: kGrey))),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
            if (hint != null) Text(hint, style: const TextStyle(color: kGrey, fontSize: 11)),
          ]),
        ),
      ]),
    );
  }
}

// ───────────── 주가 그래프 ─────────────
class PriceChartPainter extends CustomPainter {
  final List<double> prices;
  final double peak;
  final double threshold;
  PriceChartPainter(this.prices, this.peak, this.threshold);

  @override
  void paint(Canvas canvas, Size size) {
    if (prices.length < 2) return;
    final lo = math.min(prices.reduce(math.min), threshold);
    final hi = math.max(prices.reduce(math.max), peak);
    final range = (hi - lo) == 0 ? 1.0 : hi - lo;
    const pad = 8.0;
    double y(double v) => pad + (size.height - 2 * pad) * (1 - (v - lo) / range);
    double x(int i) => size.width * i / (prices.length - 1);

    dashed(canvas, y(peak), size.width, Paint()..color = kGreen..strokeWidth = 1.2);
    dashed(canvas, y(threshold), size.width, Paint()..color = kRed..strokeWidth = 1.2);

    final path = Path()..moveTo(x(0), y(prices[0]));
    for (var i = 1; i < prices.length; i++) {
      path.lineTo(x(i), y(prices[i]));
    }
    canvas.drawPath(
        path,
        Paint()
          ..color = kNavy
          ..strokeWidth = 2
          ..style = PaintingStyle.stroke);
    canvas.drawCircle(Offset(x(prices.length - 1), y(prices.last)), 4.5, Paint()..color = kRed);
  }

  void dashed(Canvas c, double yy, double w, Paint p) {
    for (double s = 0; s < w; s += 8) {
      c.drawLine(Offset(s, yy), Offset(math.min(s + 4, w), yy), p);
    }
  }

  @override
  bool shouldRepaint(covariant PriceChartPainter old) =>
      old.prices != prices || old.peak != peak || old.threshold != threshold;
}
