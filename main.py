import os
import json
import sys
import math
from datetime import datetime, timedelta, timezone
import yfinance as yf
import pandas as pd
import numpy as np
import requests

# ==========================================
# 1. 設定と定数定義
# ==========================================

def load_secrets():
    secrets_json = os.environ.get('APP_CONFIG')
    if not secrets_json:
        print("Error: APP_CONFIG environment variable is not set.")
        sys.exit(1)
    try:
        config = json.loads(secrets_json)
        # GitHub Pages URLの末尾スラッシュ補正
        if "gh" in config and not config["gh"].endswith("/"):
            config["gh"] += "/"
        return config
    except json.JSONDecodeError:
        print("Error: Failed to parse APP_CONFIG.")
        sys.exit(1)

# 米国セクターETF定義 (Clock位置は仕様書準拠)
SECTORS = [
    # NW: 回復 (Recovery)
    {"code": "XLK",  "name": "テクノロジー", "clock": 10.5, "area": "NW"},
    {"code": "XLY",  "name": "一般消費財",   "clock": 11.5, "area": "NW"},
    {"code": "XLC",  "name": "通信サービス", "clock": 9.5,  "area": "NW"},
    # NE: 好況 (Expansion)
    {"code": "XLI",  "name": "資本財",       "clock": 1.5,  "area": "NE"},
    {"code": "XLB",  "name": "素材",         "clock": 0.5,  "area": "NE"},
    {"code": "XLF",  "name": "金融",         "clock": 2.5,  "area": "NE"},
    # SE: 後退 (Slowdown)
    {"code": "XLE",  "name": "エネルギー",   "clock": 4.5,  "area": "SE"},
    {"code": "XLRE", "name": "不動産",       "clock": 5.5,  "area": "SE"},
    # SW: 不況 (Recession)
    {"code": "XLV",  "name": "ヘルスケア",   "clock": 7.5,  "area": "SW"},
    {"code": "XLP",  "name": "生活必需品",   "clock": 6.5,  "area": "SW"},
    {"code": "XLU",  "name": "公益事業",     "clock": 8.5,  "area": "SW"},
]

PHASES = {
    "回復期": {"x_sign": -1, "y_sign": 1},
    "好況期": {"x_sign": 1,  "y_sign": 1},
    "後退期": {"x_sign": 1,  "y_sign": -1},
    "不況期": {"x_sign": -1, "y_sign": -1},
}

# ==========================================
# 2. 計算ロジック
# ==========================================

def get_market_data():
    tickers = [s["code"] for s in SECTORS]
    print(f"Fetching US Sector Data: {tickers}")
    
    # 過去2年分取得
    df = yf.download(tickers, period="2y", interval="1d", progress=False)['Close']
    
    # MultiIndexカラムのフラット化（yfinanceのバージョン差異対策）
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        
    df = df.ffill().bfill()
    return df

def check_market_open(latest_date_timestamp):
    """
    データの最新日付と現在日時を比較し、休場日でないか判定する。
    実行はUTC 21:00 (米国市場クローズ後)。
    通常、最新データの日付は「実行日」と同じ(UTCベース)か「前日」であるはず。
    2日以上古い場合は休場とみなして中断する。
    """
    latest_date = latest_date_timestamp.date()
    today = datetime.now(timezone.utc).date()
    
    diff = (today - latest_date).days
    print(f"Data Date: {latest_date}, Execution Date(UTC): {today}, Diff: {diff} days")
    
    if diff > 1:
        print("Warning: Market data is not fresh (Holiday or Closed). Aborting update.")
        return False
    return True

def clock_to_rad(clock_hour):
    degree = 90 - (clock_hour * 30)
    return math.radians(degree)

def calculate_vector(df, target_date):
    data_until = df[df.index <= target_date]
    if len(data_until) < 200:
        return None, None
    
    current_prices = data_until.iloc[-1]
    ma200 = data_until.iloc[-200:].mean()
    deviations = (current_prices - ma200) / ma200 * 100
    
    total_x = 0
    total_y = 0
    
    for sector in SECTORS:
        code = sector["code"]
        if code not in deviations: continue
        
        strength = deviations[code]
        rad = clock_to_rad(sector["clock"])
        
        x = strength * math.cos(rad)
        y = strength * math.sin(rad)
        
        total_x += x
        total_y += y
        
    # スケール調整 (US ETF向け)
    scale_factor = 3.5 
    return total_x / scale_factor, total_y / scale_factor

# ==========================================
# 3. HTML生成 (GitHub Pages用)
# ==========================================

def create_standalone_html(history_points, current_point, last_date_str):
    history_json = json.dumps(history_points)
    current_json = json.dumps([current_point])
    
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>US Sector Cycle Chart</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ margin: 0; padding: 0; display: flex; justify-content: center; align-items: center; height: 100vh; background-color: #fff; font-family: sans-serif; }}
        .chart-container {{ position: relative; width: 100vw; max-width: 600px; aspect-ratio: 1; }}
        canvas {{ width: 100% !important; height: 100% !important; }}
    </style>
</head>
<body>
    <div class="chart-container">
        <canvas id="usSectorCycleChart"></canvas>
    </div>
    <script>
    document.addEventListener("DOMContentLoaded", function() {{
        var ctx = document.getElementById('usSectorCycleChart');
        
        const sectorLabels = {{
            NW: ["テクノロジー", "一般消費財", "通信"],
            NE: ["資本財", "素材", "金融"],
            SE: ["エネルギー", "不動産"],
            SW: ["ヘルスケア", "生活必需品", "公益"]
        }};

        var bgPlugin = {{
            id: 'bgPlugin',
            beforeDraw: function(chart) {{
                var ctx = chart.ctx;
                var ca = chart.chartArea;
                var x = chart.scales.x;
                var y = chart.scales.y;
                var midX = x.getPixelForValue(0);
                var midY = y.getPixelForValue(0);
                
                ctx.save();
                
                // 背景色
                // NW: 回復 (Green/Cyan)
                ctx.fillStyle = 'rgba(225, 250, 240, 0.5)';
                ctx.fillRect(ca.left, ca.top, midX - ca.left, midY - ca.top);
                
                // NE: 好況 (Red/Orange)
                ctx.fillStyle = 'rgba(255, 235, 235, 0.5)';
                ctx.fillRect(midX, ca.top, ca.left + ca.width - midX, midY - ca.top);
                
                // SE: 後退 (Yellow/Amber)
                ctx.fillStyle = 'rgba(255, 252, 230, 0.5)';
                ctx.fillRect(midX, midY, ca.left + ca.width - midX, ca.top + ca.height - midY);
                
                // SW: 不況 (Blue/Gray)
                ctx.fillStyle = 'rgba(235, 235, 250, 0.5)';
                ctx.fillRect(ca.left, midY, midX - ca.left, ca.top + ca.height - midY);
                
                // 十字線
                ctx.strokeStyle = 'rgba(0,0,0,0.2)';
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(midX, ca.top); ctx.lineTo(midX, ca.bottom);
                ctx.moveTo(ca.left, midY); ctx.lineTo(ca.right, midY);
                ctx.stroke();

                // テキスト描画
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.font = 'bold 16px sans-serif';
                ctx.fillStyle = 'rgba(0,0,0,0.4)';
                
                ctx.fillText('回復期', (ca.left + midX)/2, (ca.top + midY)/2);
                ctx.fillText('好況期', (midX + ca.right)/2, (ca.top + midY)/2);
                ctx.fillText('後退期', (midX + ca.right)/2, (midY + ca.bottom)/2);
                ctx.fillText('不況期', (ca.left + midX)/2, (midY + ca.bottom)/2);

                // 四隅の業種名
                ctx.font = '10px sans-serif';
                ctx.fillStyle = 'rgba(0,0,0,0.5)';
                var pad = 10;
                var lh = 12;

                // NW
                ctx.textAlign = 'left'; ctx.textBaseline = 'top';
                sectorLabels.NW.forEach((t, i) => ctx.fillText(t, ca.left + pad, ca.top + pad + (i * lh)));
                // NE
                ctx.textAlign = 'right';
                sectorLabels.NE.forEach((t, i) => ctx.fillText(t, ca.right - pad, ca.top + pad + (i * lh)));
                // SE
                ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
                sectorLabels.SE.slice().reverse().forEach((t, i) => ctx.fillText(t, ca.right - pad, ca.bottom - pad - (i * lh)));
                // SW
                ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
                sectorLabels.SW.slice().reverse().forEach((t, i) => ctx.fillText(t, ca.left + pad, ca.bottom - pad - (i * lh)));

                ctx.restore();
            }}
        }};

        new Chart(ctx, {{
            type: 'scatter',
            data: {{
                datasets: [
                    {{
                        // 軌跡 (線のみ)
                        label: '軌跡',
                        data: {history_json},
                        borderWidth: 2,
                        pointRadius: 0, // 点は描画しない
                        showLine: true,
                        segment: {{
                            // 過去から現在に向かって濃くなるグラデーション
                            borderColor: function(ctx) {{
                                var count = ctx.chart.data.datasets[0].data.length;
                                var val = ctx.p1DataIndex / count;
                                var alpha = 0.1 + (0.9 * val);
                                return 'rgba(80, 80, 80, ' + alpha + ')';
                            }}
                        }},
                        order: 2
                    }},
                    {{
                        // 現在地 (点のみ)
                        label: '現在',
                        data: {current_json},
                        backgroundColor: 'rgba(255, 0, 0, 1)',
                        borderColor: '#fff',
                        borderWidth: 2,
                        pointRadius: 8,
                        pointHoverRadius: 10,
                        order: 1
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{ min: -25, max: 25, grid: {{ display: false }}, ticks: {{ display: false }} }},
                    y: {{ min: -25, max: 25, grid: {{ display: false }}, ticks: {{ display: false }} }}
                }},
                plugins: {{ legend: {{display: false}}, tooltip: {{enabled: false}} }}
            }},
            plugins: [bgPlugin]
        }});
    }});
    </script>
</body>
</html>"""
    return html

def generate_wp_content(config, last_date_str, current_phase):
    pages_url = config.get("gh", "#")
    timestamp = datetime.now().strftime('%Y%m%d%H%M')
    iframe_src = f"{pages_url}index.html?v={timestamp}"

    # セクター解説
    details_html = """
    <div style="font-size:0.9em; margin-top:15px; background:#f9f9f9; padding:10px; border:1px solid #eee; border-radius:4px;">
        <p><strong>採用セクター (S&P500)</strong></p>
        <ul style="padding-left: 20px; margin-top:5px; list-style-type: disc;">
            <li><strong>回復期:</strong> テクノロジー(XLK), 一般消費財(XLY), 通信(XLC)</li>
            <li><strong>好況期:</strong> 資本財(XLI), 素材(XLB), 金融(XLF)</li>
            <li><strong>後退期:</strong> エネルギー(XLE), 不動産(XLRE)</li>
            <li><strong>不況期:</strong> ヘルスケア(XLV), 生活必需品(XLP), 公益事業(XLU)</li>
        </ul>
        <p style="margin-top:10px; font-size:0.85em; color:#666;">
            ※各ETFの200日移動平均線からの乖離率を基に算出。<br>
            ※中心から離れるほどトレンドが強く、中心に近いほど方向感がありません。
        </p>
    </div>
    """

    wp_html = f"""
    <h3>米国市場 セクターローテーション {last_date_str}</h3>
    <p>現在の重心は<strong>【{current_phase}】</strong>エリアにあります。<br>
    S&P500主要11セクターのモメンタムを解析し、過去1年間の景気循環の軌跡を描画しています。<br>中心から離れるほどトレンドが強く、中心に近いほど方向感がないことを意味します。</p>
    <div style="width: 100%; max-width: 600px; aspect-ratio: 1; margin: 0 auto; border: 1px solid #eee; overflow: hidden; box-shadow: 0 2px 5px rgba(0,0,0,0.05);">
        <iframe src="{iframe_src}" width="100%" height="100%" style="border:none; display:block;" title="US Sector Cycle Chart"></iframe>
    </div>
    <div style="height:20px" aria-hidden="true" class="wp-block-spacer"></div>
    
    <details class="wp-block-details" style="border: 1px solid #ddd; padding: 10px; cursor: pointer;">
        <summary style="font-weight: bold; outline: none;">▼ 詳細データと解説（クリックで開閉）</summary>
        {details_html}
    </details>
    """
    return wp_html

# ==========================================
# 4. メイン処理
# ==========================================

def main():
    config = load_secrets()
    
    # データ取得
    try:
        df = get_market_data()
    except Exception as e:
        print(f"Error fetching data: {e}")
        sys.exit(1)

    # 休日判定
    latest_date_timestamp = df.index[-1]
    if not check_market_open(latest_date_timestamp):
        sys.exit(0) # エラーではなく正常終了として処理をスキップ

    last_date_str = latest_date_timestamp.strftime('%Y年%m月%d日')
    
    # 軌跡計算 (365日前から10日刻み)
    history_points = []
    end_date = latest_date_timestamp
    start_date = end_date - timedelta(days=365)
    
    # 10日ごとの日付生成
    dates = pd.date_range(start=start_date, end=end_date, freq='10D')
    
    for d in dates:
        # データが存在する直近の日付を探す
        if d not in df.index:
            past_matches = df.index[df.index <= d]
            if len(past_matches) == 0: continue
            valid_date = past_matches[-1]
        else:
            valid_date = d  
            
        x, y = calculate_vector(df, valid_date)
        if x is not None:
            history_points.append({"x": round(x, 2), "y": round(y, 2)})
            
    # 現在地計算 (最新日付)
    curr_x, curr_y = calculate_vector(df, latest_date_timestamp)
    if curr_x is None:
        print("Error: Calculation failed.")
        sys.exit(1)

    current_point = {"x": round(curr_x, 2), "y": round(curr_y, 2)}
    
    # 軌跡の最後に現在地を追加してつなげる
    history_points.append(current_point)

    # フェーズ判定
    current_phase = "不明"
    c_x_sign = 1 if curr_x >= 0 else -1
    c_y_sign = 1 if curr_y >= 0 else -1
    for name, signs in PHASES.items():
        if signs["x_sign"] == c_x_sign and signs["y_sign"] == c_y_sign:
            current_phase = name
            break
            
    print(f"US Market Phase: {current_phase} (x={curr_x:.2f}, y={curr_y:.2f})")

    # GitHub Pages用HTML生成
    chart_html = create_standalone_html(history_points, current_point, last_date_str)
    
    output_dir = "public"
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(chart_html)
    print(f"Generated public/index.html")

    # WordPress更新
    wp_content = generate_wp_content(config, last_date_str, current_phase)
    
    # キー名は短縮形: h=URL, pid=PageID, u=User, p=Pass
    wp_url = f"{config['h']}/wp-json/wp/v2/pages/{config['pid']}"
    auth = (config['u'], config['p'])
    payload = {'content': wp_content}
    
    print(f"Updating WordPress Page ID: {config['pid']}...")
    try:
        response = requests.post(wp_url, json=payload, auth=auth)
        response.raise_for_status()
        print("Success! WordPress updated.")
    except requests.exceptions.RequestException as e:
        print(f"Error updating WordPress: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
