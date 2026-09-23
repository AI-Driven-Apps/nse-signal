"""
Visual bits the reel leans on that Streamlit doesn't give you for free:
Indian lakh/crore number formatting, and the horizontal "marker on a bar"
widgets for day-range and OI-range. Plain HTML/CSS rendered via
st.markdown(..., unsafe_allow_html=True) — no extra JS dependency.
"""


def format_inr_compact(value: float | None) -> str:
    """1234567 -> '12.35L', 12345678 -> '1.23Cr', -6045 -> '-6,045'."""
    if value is None:
        return "—"
    sign = "-" if value < 0 else "+" if value > 0 else ""
    v = abs(value)
    if v >= 1_00_00_000:  # 1 crore
        return f"{sign}{v / 1_00_00_000:.2f}Cr"
    if v >= 1_00_000:  # 1 lakh
        return f"{sign}{v / 1_00_000:.2f}L"
    return f"{sign}{v:,.0f}"


def range_bar_html(current: float, low: float, high: float, height_px: int = 6) -> str:
    """
    Horizontal bar with a marker showing where `current` sits between
    `low` and `high` — the reel's Day Range column.
    """
    if low is None or high is None or current is None or high <= low:
        return "<div style='color:#666'>—</div>"
    pct = max(0.0, min(1.0, (current - low) / (high - low))) * 100
    return f"""
    <div style="position:relative;width:100%;height:{height_px}px;
                background:#2a2a2a;border-radius:3px;margin:2px 0;">
      <div style="position:absolute;left:{pct}%;top:-2px;width:2px;height:{height_px+4}px;
                  background:#e0e0e0;border-radius:1px;"></div>
    </div>
    """


def oi_range_bar_html(oi_change: float, max_abs: float, height_px: int = 6) -> str:
    """
    OI-change bar, colored red (short buildup) / green (long buildup),
    scaled against the widest OI change in the current view — the reel's
    OI Range column, with the Cr/L-formatted value alongside.
    """
    if oi_change is None or max_abs in (None, 0):
        return "<div style='color:#666'>—</div>"
    color = "#2ecc71" if oi_change >= 0 else "#e74c3c"
    width_pct = min(100.0, abs(oi_change) / max_abs * 100)
    label = format_inr_compact(oi_change)
    return f"""
    <div style="display:flex;align-items:center;gap:6px;">
      <div style="position:relative;width:80px;height:{height_px}px;
                  background:#2a2a2a;border-radius:3px;">
        <div style="position:absolute;{'left' if oi_change >= 0 else 'right'}:50%;
                    width:{width_pct/2}%;height:{height_px}px;background:{color};
                    border-radius:3px;"></div>
      </div>
      <span style="color:{color};font-size:0.85em;">{label}</span>
    </div>
    """


def strength_gauge_html(score: int, label: str) -> str:
    """0-100 red-yellow-green gauge, matching the reel's 'Strength 74' bar."""
    color = "#2ecc71" if score >= 65 else "#e74c3c" if score <= 35 else "#f1c40f"
    return f"""
    <div>
      <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
        <span style="font-weight:600;">Strength {score}</span>
        <span style="color:{color};font-weight:600;">{label}</span>
      </div>
      <div style="width:100%;height:10px;border-radius:5px;
                  background:linear-gradient(90deg,#e74c3c,#f1c40f,#2ecc71);
                  position:relative;">
        <div style="position:absolute;left:{score}%;top:-3px;width:3px;height:16px;
                    background:#fff;border-radius:1px;"></div>
      </div>
    </div>
    """


def sparkline_svg(values: list[float], width: int = 90, height: int = 24) -> str:
    """
    Tiny inline SVG line chart from real minute-candle closes — the reel's
    Trend column. Returns an em-dash placeholder if there's no real series
    yet (see DhanMarketClient.get_intraday_minute_data); never fabricates
    a fake trend line.
    """
    if not values or len(values) < 2:
        return "<span style='color:#666'>—</span>"
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    step = width / (len(values) - 1)
    points = " ".join(
        f"{i*step:.1f},{height - (v - lo) / span * height:.1f}"
        for i, v in enumerate(values)
    )
    color = "#2ecc71" if values[-1] >= values[0] else "#e74c3c"
    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.5"/>'
        f'</svg>'
    )
