import json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from database import get_db, close_db, init_db

app = Flask(__name__)
app.secret_key = "poker-dashboard-local"
app.teardown_appcontext(close_db)


@app.template_filter("nok")
def nok_filter(value):
    if value is None:
        return "0 kr"
    v = float(value)
    formatted = f"{abs(v):,.0f}".replace(",", " ")
    return f"{'-' if v < 0 else ''}{formatted} kr"


@app.template_filter("pnl")
def pnl_filter(value):
    if value is None:
        return "0 kr"
    v = float(value)
    formatted = f"{abs(v):,.0f}".replace(",", " ")
    if v > 0:
        return f"+{formatted} kr"
    elif v < 0:
        return f"-{formatted} kr"
    return "0 kr"


with app.app_context():
    init_db()


# ── Stats ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    club_id       = request.args.get("club_id", type=int)
    game_id       = request.args.get("game_id", type=int)
    blind_level_id = request.args.get("blind_level_id", type=int)
    db = get_db()
    clubs = db.execute("SELECT * FROM clubs ORDER BY name").fetchall()

    # Stake table always filtered by club only (used to pick a stake filter)
    club_cond   = "WHERE s.club_id = ?" if club_id else ""
    club_params = (club_id,) if club_id else ()

    stake_stats = db.execute(f"""
        SELECT (g.name || ' ' || bl.name) as name,
               g.id as game_id, bl.id as blind_level_id, bl.big_blind,
               COALESCE(SUM(s.profit), 0) as profit,
               SUM(s.hands) as hands,
               CASE WHEN SUM(s.hands) > 0
                    THEN SUM(s.profit / bl.big_blind) / SUM(s.hands) * 100
                    ELSE NULL END as bb100
        FROM sessions s
        JOIN games g ON s.game_id = g.id
        JOIN blind_levels bl ON s.blind_level_id = bl.id
        {club_cond}
        GROUP BY g.id, bl.id
        ORDER BY g.name, bl.big_blind
    """, club_params).fetchall()

    # Full filter for stats, chart, recent sessions
    conditions, params = [], []
    if club_id:
        conditions.append("s.club_id = ?")
        params.append(club_id)
    if game_id:
        conditions.append("s.game_id = ?")
        params.append(game_id)
    if blind_level_id:
        conditions.append("s.blind_level_id = ?")
        params.append(blind_level_id)
    cond = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    stats = db.execute(f"""
        SELECT
            COUNT(*) as total_sessions,
            COALESCE(SUM(profit), 0) as total_profit,
            COALESCE(SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END), 0) as wins,
            COALESCE(MAX(profit), 0) as best_session,
            COALESCE(MIN(profit), 0) as worst_session
        FROM sessions s {cond}
    """, params).fetchone()

    daily_pnl = db.execute(f"""
        SELECT date, SUM(profit) as profit, COUNT(*) as session_count, SUM(hands) as hands
        FROM sessions s {cond}
        GROUP BY date
        ORDER BY date DESC
    """, params).fetchall()

    _session_rows = db.execute(f"""
        SELECT s.id, s.date, c.name as club_name, g.name as game_name,
               bl.name as blind_name, s.hands, s.profit
        FROM sessions s
        JOIN clubs c ON s.club_id = c.id
        JOIN games g ON s.game_id = g.id
        JOIN blind_levels bl ON s.blind_level_id = bl.id
        {cond}
        ORDER BY s.date DESC, s.id DESC
    """, params).fetchall()

    sessions_by_date: dict = {}
    for row in _session_rows:
        sessions_by_date.setdefault(row["date"], []).append(row)

    chart_rows = db.execute(f"""
        SELECT date, profit
        FROM sessions s {cond}
        ORDER BY date ASC, id ASC
    """, params).fetchall()

    cum = 0
    chart_labels, chart_values = [], []
    daily: dict = {}
    for row in chart_rows:
        cum += row["profit"]
        chart_labels.append(row["date"])
        chart_values.append(round(cum, 2))
        daily[row["date"]] = round(daily.get(row["date"], 0) + row["profit"], 2)

    daily_labels = list(daily.keys())
    daily_values = list(daily.values())

    club_breakdown = None
    if not club_id:
        club_breakdown = db.execute("""
            SELECT c.id, c.name,
                COALESCE((SELECT COUNT(*) FROM sessions WHERE club_id = c.id), 0) as session_count,
                COALESCE((SELECT SUM(hands) FROM sessions WHERE club_id = c.id AND hands IS NOT NULL), 0) as total_hands,
                COALESCE((SELECT SUM(profit) FROM sessions WHERE club_id = c.id), 0) as session_profit,
                (SELECT CASE WHEN SUM(s.hands) > 0
                             THEN SUM(s.profit / bl.big_blind) / SUM(s.hands) * 100
                             ELSE NULL END
                 FROM sessions s JOIN blind_levels bl ON s.blind_level_id = bl.id
                 WHERE s.club_id = c.id AND s.hands IS NOT NULL AND s.hands > 0) as bb100,
                COALESCE((SELECT SUM(amount) FROM transactions WHERE club_id = c.id AND type = 'rakeback'), 0) as rakeback,
                COALESCE((SELECT SUM(CASE
                    WHEN type IN ('deposit', 'misc') THEN amount
                    WHEN type = 'withdrawal' THEN -amount
                    ELSE 0
                END) FROM transactions WHERE club_id = c.id), 0) as net_cash,
                COALESCE((SELECT SUM(amount) FROM transactions WHERE to_club_id = c.id AND type = 'swap'), 0) as swaps_in,
                COALESCE((SELECT SUM(amount) FROM transactions WHERE club_id = c.id AND type = 'swap'), 0) as swaps_out
            FROM clubs c ORDER BY c.name
        """).fetchall()

    return render_template("index.html",
        clubs=clubs, selected_club_id=club_id,
        selected_game_id=game_id, selected_blind_level_id=blind_level_id,
        stats=stats,
        stake_stats=stake_stats, club_breakdown=club_breakdown, daily_pnl=daily_pnl,
        sessions_by_date=sessions_by_date,
        chart_labels=json.dumps(chart_labels),
        chart_values=json.dumps(chart_values),
        daily_labels=json.dumps(daily_labels),
        daily_values=json.dumps(daily_values))


# ── Clubs ────────────────────────────────────────────────────────────────────

@app.route("/clubs", methods=["GET", "POST"])
def clubs():
    db = get_db()
    if request.method == "POST":
        name = request.form["name"].strip()
        notes = request.form.get("notes", "").strip()
        try:
            db.execute("INSERT INTO clubs (name, notes) VALUES (?, ?)", (name, notes))
            db.commit()
            flash(f"'{name}' added.", "success")
        except Exception as e:
            flash(f"Error: {e}", "danger")
        return redirect(url_for("clubs"))

    clubs_list = db.execute("""
        SELECT c.*,
               COUNT(DISTINCT s.id) as session_count,
               COALESCE(SUM(s.profit), 0) as total_profit
        FROM clubs c
        LEFT JOIN sessions s ON s.club_id = c.id
        GROUP BY c.id ORDER BY c.name
    """).fetchall()
    return render_template("clubs.html", clubs=clubs_list)


@app.route("/clubs/<int:club_id>/delete", methods=["POST"])
def delete_club(club_id):
    db = get_db()
    try:
        db.execute("DELETE FROM clubs WHERE id = ?", (club_id,))
        db.commit()
        flash("Club deleted.", "success")
    except Exception as e:
        flash(f"Cannot delete: {e}", "danger")
    return redirect(url_for("clubs"))


# ── Stakes (game types + blind levels) ───────────────────────────────────────

@app.route("/stakes", methods=["GET", "POST"])
def stakes():
    db = get_db()
    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "game":
            name = request.form["name"].strip()
            try:
                db.execute("INSERT INTO games (name) VALUES (?)", (name,))
                db.commit()
                flash(f"Game '{name}' added.", "success")
            except Exception as e:
                flash(f"Error: {e}", "danger")
        elif form_type == "blind":
            name = request.form["name"].strip()
            sb = float(request.form["small_blind"])
            bb = float(request.form["big_blind"])
            try:
                db.execute("INSERT INTO blind_levels (name, small_blind, big_blind) VALUES (?, ?, ?)", (name, sb, bb))
                db.commit()
                flash(f"Blind level '{name}' added.", "success")
            except Exception as e:
                flash(f"Error: {e}", "danger")
        return redirect(url_for("stakes"))

    games = db.execute("""
        SELECT g.*, COUNT(s.id) as session_count
        FROM games g
        LEFT JOIN sessions s ON s.game_id = g.id
        GROUP BY g.id ORDER BY g.name
    """).fetchall()
    blind_levels = db.execute("""
        SELECT bl.*, COUNT(s.id) as session_count
        FROM blind_levels bl
        LEFT JOIN sessions s ON s.blind_level_id = bl.id
        GROUP BY bl.id ORDER BY bl.big_blind
    """).fetchall()
    return render_template("stakes.html", games=games, blind_levels=blind_levels)


@app.route("/games/<int:game_id>/delete", methods=["POST"])
def delete_game(game_id):
    db = get_db()
    try:
        db.execute("DELETE FROM games WHERE id = ?", (game_id,))
        db.commit()
        flash("Game type deleted.", "success")
    except Exception as e:
        flash(f"Cannot delete: {e}", "danger")
    return redirect(url_for("stakes"))


@app.route("/blind_levels/<int:level_id>/delete", methods=["POST"])
def delete_blind_level(level_id):
    db = get_db()
    try:
        db.execute("DELETE FROM blind_levels WHERE id = ?", (level_id,))
        db.commit()
        flash("Blind level deleted.", "success")
    except Exception as e:
        flash(f"Cannot delete: {e}", "danger")
    return redirect(url_for("stakes"))


# ── Sessions ─────────────────────────────────────────────────────────────────

@app.route("/sessions")
def sessions():
    club_id = request.args.get("club_id", type=int)
    game_id = request.args.get("game_id", type=int)
    db = get_db()
    clubs = db.execute("SELECT * FROM clubs ORDER BY name").fetchall()
    games = db.execute("SELECT * FROM games ORDER BY name").fetchall()
    blind_levels = db.execute("SELECT * FROM blind_levels ORDER BY big_blind").fetchall()

    conditions, params = [], []
    if club_id:
        conditions.append("s.club_id = ?")
        params.append(club_id)
    if game_id:
        conditions.append("s.game_id = ?")
        params.append(game_id)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sessions_list = db.execute(f"""
        SELECT s.*, c.name as club_name,
               g.name as game_name, bl.name as blind_name,
               (g.name || ' ' || bl.name) as stake_name
        FROM sessions s
        JOIN clubs c ON s.club_id = c.id
        JOIN games g ON s.game_id = g.id
        JOIN blind_levels bl ON s.blind_level_id = bl.id
        {where}
        ORDER BY s.date DESC, s.id DESC
    """, params).fetchall()

    today = datetime.now().strftime("%Y-%m-%d")
    return render_template("sessions.html",
        sessions=sessions_list, clubs=clubs, games=games, blind_levels=blind_levels,
        selected_club_id=club_id, selected_game_id=game_id, today=today)


@app.route("/sessions/add", methods=["POST"])
def add_session():
    db = get_db()
    dates       = request.form.getlist("date[]")
    club_ids    = request.form.getlist("club_id[]")
    game_ids    = request.form.getlist("game_id[]")
    blind_ids   = request.form.getlist("blind_level_id[]")
    hands_list  = request.form.getlist("hands[]")
    profits     = request.form.getlist("profit[]")

    added = 0
    for i in range(len(dates)):
        if not dates[i] or profits[i] == "":
            continue
        hands = int(hands_list[i]) if i < len(hands_list) and hands_list[i] else None
        db.execute("""
            INSERT INTO sessions (club_id, game_id, blind_level_id, date, hands, profit)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            int(club_ids[i]),
            int(game_ids[i]),
            int(blind_ids[i]),
            dates[i],
            hands,
            float(profits[i]),
        ))
        added += 1

    db.commit()
    flash(f"{added} session{'s' if added != 1 else ''} added.", "success")
    return redirect(url_for("sessions"))


@app.route("/sessions/<int:session_id>/edit", methods=["GET", "POST"])
def edit_session(session_id):
    db = get_db()
    if request.method == "POST":
        hands = request.form.get("hands") or None
        db.execute("""
            UPDATE sessions
            SET club_id=?, game_id=?, blind_level_id=?, date=?, hands=?, profit=?
            WHERE id=?
        """, (
            int(request.form["club_id"]),
            int(request.form["game_id"]),
            int(request.form["blind_level_id"]),
            request.form["date"],
            int(hands) if hands else None,
            float(request.form["profit"]),
            session_id,
        ))
        db.commit()
        flash("Session updated.", "success")
        return redirect(url_for("sessions"))

    sess = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    clubs = db.execute("SELECT * FROM clubs ORDER BY name").fetchall()
    games = db.execute("SELECT * FROM games ORDER BY name").fetchall()
    blind_levels = db.execute("SELECT * FROM blind_levels ORDER BY big_blind").fetchall()
    return render_template("edit_session.html", sess=sess, clubs=clubs,
                           games=games, blind_levels=blind_levels)


@app.route("/sessions/<int:session_id>/delete", methods=["POST"])
def delete_session(session_id):
    db = get_db()
    db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    db.commit()
    flash("Session deleted.", "success")
    return redirect(url_for("sessions"))


# ── Transactions ─────────────────────────────────────────────────────────────

@app.route("/transactions", methods=["GET", "POST"])
def transactions():
    club_id = request.args.get("club_id", type=int)
    db = get_db()
    clubs = db.execute("SELECT * FROM clubs ORDER BY name").fetchall()

    if request.method == "POST":
        txn_type = request.form["type"]
        raw_to = request.form.get("to_club_id", "").strip()
        to_club_id = int(raw_to) if raw_to else None
        db.execute("""
            INSERT INTO transactions (club_id, to_club_id, type, amount, date, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            int(request.form["club_id"]),
            to_club_id,
            txn_type,
            float(request.form["amount"]),
            request.form["date"],
            request.form.get("notes", "").strip(),
        ))
        db.commit()
        flash("Transaction added.", "success")
        return redirect(url_for("transactions", club_id=request.form["club_id"]))

    where_parts, params = [], []
    if club_id:
        where_parts.append("(t.club_id = ? OR t.to_club_id = ?)")
        params.extend([club_id, club_id])
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    txns = db.execute(f"""
        SELECT t.*, c.name as club_name, tc.name as to_club_name
        FROM transactions t
        JOIN clubs c ON t.club_id = c.id
        LEFT JOIN clubs tc ON t.to_club_id = tc.id
        {where}
        ORDER BY t.date DESC, t.id DESC
    """, params).fetchall()

    today = datetime.now().strftime("%Y-%m-%d")
    return render_template("transactions.html",
        transactions=txns, clubs=clubs,
        selected_club_id=club_id, today=today)


@app.route("/transactions/<int:txn_id>/delete", methods=["POST"])
def delete_transaction(txn_id):
    db = get_db()
    db.execute("DELETE FROM transactions WHERE id=?", (txn_id,))
    db.commit()
    flash("Transaction deleted.", "success")
    return redirect(url_for("transactions"))


# ── Swaps (redirected to transactions) ───────────────────────────────────────

@app.route("/swaps", methods=["GET", "POST"])
def swaps():
    return redirect(url_for("transactions"))


@app.route("/swaps/<int:swap_id>/delete", methods=["POST"])
def delete_swap(swap_id):
    return redirect(url_for("transactions"))


# ── Historical sessions ───────────────────────────────────────────────────────

@app.route("/history", methods=["GET", "POST"])
def history():
    db = get_db()
    games = db.execute("SELECT * FROM games ORDER BY name").fetchall()
    blind_levels = db.execute("SELECT * FROM blind_levels ORDER BY big_blind").fetchall()

    if request.method == "POST":
        game_ids    = request.form.getlist("game_id[]")
        blind_ids   = request.form.getlist("blind_level_id[]")
        hands_list  = request.form.getlist("hands[]")
        profits     = request.form.getlist("profit[]")
        currencies  = request.form.getlist("currency[]")

        added = 0
        for i in range(len(profits)):
            if profits[i] == "":
                continue
            hands = int(hands_list[i]) if i < len(hands_list) and hands_list[i] else None
            db.execute("""
                INSERT INTO historical_sessions (game_id, blind_level_id, hands, profit, currency)
                VALUES (?, ?, ?, ?, ?)
            """, (
                int(game_ids[i]),
                int(blind_ids[i]),
                hands,
                float(profits[i]),
                currencies[i] if i < len(currencies) else "NOK",
            ))
            added += 1

        db.commit()
        flash(f"{added} historical session{'s' if added != 1 else ''} added.", "success")
        return redirect(url_for("history"))

    history_list = db.execute("""
        SELECT h.*, g.name as game_name, bl.name as blind_name,
               (g.name || ' ' || bl.name) as stake_name
        FROM historical_sessions h
        JOIN games g ON h.game_id = g.id
        JOIN blind_levels bl ON h.blind_level_id = bl.id
        ORDER BY h.id DESC
    """).fetchall()

    return render_template("history.html",
        history=history_list, games=games, blind_levels=blind_levels)


@app.route("/history/<int:entry_id>/delete", methods=["POST"])
def delete_history(entry_id):
    db = get_db()
    db.execute("DELETE FROM historical_sessions WHERE id=?", (entry_id,))
    db.commit()
    flash("Entry deleted.", "success")
    return redirect(url_for("history"))


# ── All-time stats ────────────────────────────────────────────────────────────

@app.route("/all-time")
def all_time():
    db = get_db()
    game_id = request.args.get("game_id", type=int)
    games = db.execute("SELECT * FROM games ORDER BY name").fetchall()

    gf = "AND g.id = ?" if game_id else ""
    gp = (game_id,) if game_id else ()

    def query_stats(table, currency_filter=None):
        conditions, params = [], []
        if currency_filter:
            conditions.append("s.currency = ?")
            params.append(currency_filter)
        if game_id:
            conditions.append("g.id = ?")
            params.append(game_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        join = "JOIN games g ON s.game_id = g.id" if game_id else ""
        return db.execute(f"""
            SELECT COUNT(*) as total_sessions,
                   COALESCE(SUM(profit), 0) as total_profit,
                   COALESCE(SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END), 0) as wins,
                   COALESCE(MAX(profit), 0) as best_session,
                   COALESCE(MIN(profit), 0) as worst_session
            FROM {table} s {join} {where}
        """, params).fetchone()

    sessions_2026 = query_stats("sessions")
    hist_nok      = query_stats("historical_sessions", currency_filter="NOK")
    hist_usd      = query_stats("historical_sessions", currency_filter="USD")

    nok_by_game = db.execute(f"""
        SELECT (g.name || ' ' || bl.name) as name,
               bl.big_blind,
               SUM(s.profit) as profit,
               SUM(s.hands) as hands
        FROM (
            SELECT game_id, blind_level_id, profit, hands FROM sessions
            UNION ALL
            SELECT game_id, blind_level_id, profit, hands FROM historical_sessions WHERE currency='NOK'
        ) s
        JOIN games g ON s.game_id = g.id
        JOIN blind_levels bl ON s.blind_level_id = bl.id
        WHERE 1=1 {gf}
        GROUP BY g.id, bl.id ORDER BY g.name, bl.big_blind
    """, gp).fetchall()

    usd_by_game = db.execute(f"""
        SELECT (g.name || ' ' || bl.name) as name,
               bl.big_blind,
               SUM(h.profit) as profit,
               SUM(h.hands) as hands
        FROM historical_sessions h
        JOIN games g ON h.game_id = g.id
        JOIN blind_levels bl ON h.blind_level_id = bl.id
        WHERE h.currency = 'USD' {gf}
        GROUP BY g.id, bl.id ORDER BY g.name, bl.big_blind
    """, gp).fetchall()

    nok_agg = db.execute(f"""
        SELECT SUM(s.hands) as total_hands,
               CASE WHEN SUM(s.hands) > 0
                    THEN SUM(s.profit / bl.big_blind) / SUM(s.hands) * 100
                    ELSE NULL END as bb100
        FROM (
            SELECT game_id, blind_level_id, profit, hands FROM sessions
             WHERE hands IS NOT NULL AND hands > 0
            UNION ALL
            SELECT game_id, blind_level_id, profit, hands FROM historical_sessions
             WHERE currency='NOK' AND hands IS NOT NULL AND hands > 0
        ) s
        JOIN blind_levels bl ON s.blind_level_id = bl.id
        JOIN games g ON s.game_id = g.id
        WHERE 1=1 {gf}
    """, gp).fetchone()

    usd_agg = db.execute(f"""
        SELECT SUM(h.hands) as total_hands,
               CASE WHEN SUM(h.hands) > 0
                    THEN SUM(h.profit / bl.big_blind) / SUM(h.hands) * 100
                    ELSE NULL END as bb100
        FROM historical_sessions h
        JOIN blind_levels bl ON h.blind_level_id = bl.id
        JOIN games g ON h.game_id = g.id
        WHERE h.currency = 'USD' AND h.hands IS NOT NULL AND h.hands > 0 {gf}
    """, gp).fetchone()

    return render_template("all_time.html",
        sessions_2026=sessions_2026,
        hist_nok=hist_nok,
        hist_usd=hist_usd,
        nok_by_game=nok_by_game,
        usd_by_game=usd_by_game,
        nok_agg=nok_agg,
        usd_agg=usd_agg,
        games=games,
        selected_game_id=game_id)


if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True, port=5000)
