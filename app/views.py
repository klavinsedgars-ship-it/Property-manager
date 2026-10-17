import csv
import io
from datetime import date

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for

from . import finance as fin
from . import market
from . import services as svc
from . import storage
from .db import get_db, set_setting, settings

bp = Blueprint("main", __name__)

PROPERTY_FIELDS = ["name", "address", "city", "district", "kind", "building_series", "area_m2", "rooms",
                   "floor", "cadastre_no", "status", "expected_rent", "purchase_date", "purchase_price", "purchase_costs",
                   "declared_residence", "loan_amount", "loan_rate", "loan_term_months", "loan_start",
                   "sale_date", "sale_price", "sale_costs", "notes"]
NUMERIC = {"expected_rent", "area_m2", "rooms", "purchase_price", "purchase_costs", "loan_amount", "loan_rate",
           "loan_term_months", "sale_price", "sale_costs", "declared_residence"}

KINDS = ["apartment", "house", "commercial", "land", "parking"]
STATUSES = ["renovating", "rented", "vacant", "own_use", "sold"]
# Common Latvian building series — affects market value comparisons.
SERIES = ["", "Pre-war", "Stalin era", "Khrushchev (103/316/318)", "Lithuanian project", "119", "467",
          "602", "104", "Small-family", "Renovated pre-war", "New project (post-2000)", "Private house", "Other"]
DOC_CATEGORIES = ["Purchase deed", "Land book", "Lease agreement", "Handover act", "Invoice / receipt",
                  "Insurance", "Utility", "Tax", "Photo", "Floor plan", "Other"]


def _form_values(fields):
    out = {}
    for f in fields:
        v = request.form.get(f, "").strip()
        if f == "declared_residence":
            v = 1 if request.form.get(f) else 0
        elif f in NUMERIC:
            v = float(v.replace(",", ".").replace(" ", "")) if v else None
        else:
            v = v or None
        out[f] = v
    return out


def _local(url):
    """Only allow same-site redirect targets."""
    return bool(url) and url.startswith("/") and not url.startswith("//") and "\\" not in url


def _num(name, default=0.0):
    v = request.form.get(name, "").strip().replace(",", ".").replace(" ", "")
    try:
        return float(v) if v else default
    except ValueError:
        abort(400, f"Invalid number for {name}")


@bp.app_context_processor
def _ctx():
    return {"today": date.today(), "CATEGORIES": fin.CATEGORIES, "group_of": fin.group_of,
            "max_upload_mb": storage.max_upload_mb(), "ephemeral": current_app.config.get("EPHEMERAL")}


# --------------------------------------------------------------------------- dashboard

@bp.route("/")
def dashboard():
    bundles = svc.all_properties()
    totals = svc.portfolio_totals(bundles)
    db = get_db()
    today = date.today()
    start = fin.add_months(date(today.year, today.month, 1), -23)
    all_tx = db.execute("SELECT * FROM transactions WHERE date >= ?", (start.isoformat(),)).fetchall()
    monthly = fin.monthly_table(all_tx, None, start, today)
    # add debt service per property
    for b in bundles:
        loan = fin.Loan.from_property(b["p"])
        for d, i, pr in loan.schedule(today):
            for r in monthly:
                if r["month"] == fin.ym(d):
                    r["interest"] += i
                    r["principal"] += pr
                    r["cash_flow"] -= i + pr
    arrears = []
    for b in bundles:
        for r in fin.rent_roll(b["leases"], b["txs"], today):
            overdue = r["month"] < fin.ym(today) or today.day > 5
            if r["due"] > 0.01 and overdue:
                lease = next(l for l in b["leases"] if l["id"] == r["lease_id"])
                arrears.append({**r, "property": b["p"], "tenant": lease["tenant_name"]})
    reminders = _reminders(bundles, today)
    return render_template("dashboard.html", bundles=bundles, t=totals, monthly=monthly,
                           arrears=arrears[:15], reminders=reminders)


def _reminders(bundles, today):
    out = []
    horizon = fin.add_months(today, 2)
    for b in bundles:
        for l in b["leases"]:
            e = fin.to_date(l["end_date"])
            if e and today <= e <= horizon:
                out.append((e, f"Lease of {l['tenant_name']} at {b['p']['name']} ends"))
    for d in get_db().execute("""SELECT d.*, p.name AS pname FROM documents d LEFT JOIN properties p
                                 ON p.id = d.property_id WHERE expires_on IS NOT NULL""").fetchall():
        e = fin.to_date(d["expires_on"])
        if e and e <= horizon:
            out.append((e, f"{d['title']} ({d['pname'] or 'general'}) expires"))
    return sorted(out)


# --------------------------------------------------------------------------- properties

@bp.route("/properties")
def properties():
    return render_template("properties.html", bundles=svc.all_properties())


@bp.route("/properties/new", methods=["GET", "POST"])
def property_new():
    if request.method == "POST":
        v = _form_values(PROPERTY_FIELDS)
        if not v["name"]:
            flash("Name is required", "error")
        else:
            cols = ",".join(v)
            cur = get_db().execute(f"INSERT INTO properties ({cols}) VALUES ({','.join('?' * len(v))})",
                                   list(v.values()))
            get_db().commit()
            return redirect(url_for("main.property_detail", pid=cur.lastrowid))
    return render_template("property_form.html", p=None, kinds=KINDS, statuses=STATUSES, series=SERIES)


@bp.route("/properties/<int:pid>/edit", methods=["GET", "POST"])
def property_edit(pid):
    b = svc.load_property(pid) or abort(404)
    if request.method == "POST":
        v = _form_values(PROPERTY_FIELDS)
        if v.get("sale_price") and v["status"] != "sold":
            v["status"] = "sold"
        get_db().execute(f"UPDATE properties SET {','.join(k + '=?' for k in v)} WHERE id=?",
                         [*v.values(), pid])
        get_db().commit()
        return redirect(url_for("main.property_detail", pid=pid))
    return render_template("property_form.html", p=b["p"], kinds=KINDS, statuses=STATUSES, series=SERIES)


@bp.route("/properties/<int:pid>/delete", methods=["POST"])
def property_delete(pid):
    get_db().execute("DELETE FROM properties WHERE id=?", (pid,))
    get_db().commit()
    flash("Property deleted")
    return redirect(url_for("main.properties"))


@bp.route("/properties/<int:pid>")
def property_detail(pid):
    b = svc.load_property(pid) or abort(404)
    tab = request.args.get("tab", "overview")
    today = date.today()
    m = svc.compute(b, today)
    p = b["p"]
    ctx = {"b": b, "p": p, "m": m, "tab": tab}
    start = fin.to_date(p["purchase_date"]) or today
    end = fin.to_date(p["sale_date"]) or today
    monthly = fin.monthly_table(b["txs"], fin.Loan.from_property(p), start, max(start, end))
    ctx["monthly"] = monthly
    ctx["yearly"] = fin.yearly_table(monthly)
    if tab == "overview":
        ctx["cat_totals"] = fin.category_totals([t for t in b["txs"] if t["kind"] == "expense"])
        ctx["market"] = market.property_market_context(get_db(), p, m)
    elif tab == "money":
        ctx["filter_cat"] = request.args.get("category", "")
        ctx["filter_year"] = request.args.get("year", "")
        txs = b["txs"]
        if ctx["filter_cat"]:
            txs = [t for t in txs if t["category"] == ctx["filter_cat"]]
        if ctx["filter_year"]:
            txs = [t for t in txs if t["date"].startswith(ctx["filter_year"])]
        ctx["txs"] = txs
        ctx["years"] = sorted({t["date"][:4] for t in b["txs"]}, reverse=True)
    elif tab == "tenants":
        ctx["tenants"] = get_db().execute("SELECT * FROM tenants ORDER BY name").fetchall()
        roll = fin.rent_roll(b["leases"], b["txs"], today)
        ctx["roll"] = [r for r in roll if r["due"] > 0.01]
    elif tab == "documents":
        ctx["docs"] = get_db().execute("SELECT * FROM documents WHERE property_id=? ORDER BY uploaded_at DESC",
                                       (pid,)).fetchall()
        ctx["doc_categories"] = DOC_CATEGORIES
    elif tab == "sale":
        s = settings()
        rate, why = svc.appreciation(s)
        if request.args.get("g"):
            rate, why = float(request.args["g"]) / 100, "scenario override"
        rows = fin.sale_scenarios(p, m, b["txs"], s, today, rate, horizon=15)
        for r in rows:
            r["label"] = "Now" if r["year"] == 0 else str(r["date"].year)
        ctx.update(rows=rows, rate=rate, rate_why=why, rec=fin.recommendation(p, rows, s, today), s=s)
    return render_template("property.html", **ctx)


# --------------------------------------------------------------------------- transactions

@bp.route("/properties/<int:pid>/transactions/new", methods=["GET", "POST"])
@bp.route("/transactions/<int:tid>/edit", methods=["GET", "POST"])
def transaction_form(pid=None, tid=None):
    db = get_db()
    t = None
    if tid:
        t = db.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone() or abort(404)
        pid = t["property_id"]
    p = db.execute("SELECT * FROM properties WHERE id=?", (pid,)).fetchone() or abort(404)
    leases = db.execute("""SELECT l.*, tn.name AS tenant_name FROM leases l JOIN tenants tn ON tn.id=l.tenant_id
                           WHERE property_id=? ORDER BY start_date DESC""", (pid,)).fetchall()
    if request.method == "POST":
        category = request.form["category"]
        kind = "income" if fin.group_of(category) == "income" else "expense"
        vals = (request.form.get("date") or date.today().isoformat(), kind, category, _num("amount"),
                request.form.get("description") or None, request.form.get("vendor") or None,
                request.form.get("lease_id") or None)
        if tid:
            db.execute("""UPDATE transactions SET date=?, kind=?, category=?, amount=?, description=?, vendor=?,
                          lease_id=? WHERE id=?""", (*vals, tid))
        else:
            cur = db.execute("""INSERT INTO transactions (date, kind, category, amount, description, vendor,
                                lease_id, property_id) VALUES (?,?,?,?,?,?,?,?)""", (*vals, pid))
            tid = cur.lastrowid
        f = request.files.get("receipt")
        if f and f.filename:
            _store_document(f, pid, None, tid, f"Receipt: {request.form.get('description') or category}",
                            "Invoice / receipt", None)
        db.commit()
        if request.form.get("again"):
            flash("Saved — add another")
            return redirect(url_for("main.transaction_form", pid=pid, category=category,
                                    date=vals[0]))
        return redirect(url_for("main.property_detail", pid=pid, tab="money"))
    preset = {"category": request.args.get("category"), "date": request.args.get("date"),
              "amount": request.args.get("amount"), "lease_id": request.args.get("lease_id"),
              "description": request.args.get("description")}
    return render_template("transaction_form.html", t=t, p=p, leases=leases, preset=preset)


@bp.route("/transactions/<int:tid>/delete", methods=["POST"])
def transaction_delete(tid):
    db = get_db()
    t = db.execute("SELECT property_id FROM transactions WHERE id=?", (tid,)).fetchone() or abort(404)
    db.execute("DELETE FROM transactions WHERE id=?", (tid,))
    db.commit()
    return redirect(url_for("main.property_detail", pid=t["property_id"], tab="money"))


@bp.route("/transactions.csv")
def transactions_csv():
    rows = get_db().execute("""SELECT t.date, p.name AS property, t.kind, t.category, t.amount, t.vendor,
                               t.description FROM transactions t JOIN properties p ON p.id=t.property_id
                               ORDER BY t.date""").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "property", "kind", "category", "amount", "vendor", "description"])
    w.writerows([list(r) for r in rows])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=transactions.csv"})


# --------------------------------------------------------------------------- tenants & leases

@bp.route("/tenants")
def tenants():
    db = get_db()
    current = {}
    for r in db.execute("""SELECT l.tenant_id, p.name FROM leases l JOIN properties p ON p.id = l.property_id
                           WHERE l.end_date IS NULL OR l.end_date >= ?""", (date.today().isoformat(),)):
        current.setdefault(r["tenant_id"], []).append(r["name"])
    rows = [{**{k: t[k] for k in t.keys()}, "current_props": ", ".join(current.get(t["id"], []))}
            for t in db.execute("SELECT * FROM tenants ORDER BY name")]
    return render_template("tenants.html", tenants=rows)


@bp.route("/tenants/new", methods=["GET", "POST"])
@bp.route("/tenants/<int:tid>/edit", methods=["GET", "POST"])
def tenant_form(tid=None):
    db = get_db()
    t = db.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone() if tid else None
    if request.method == "POST":
        vals = [request.form.get(k) or None for k in ("name", "phone", "email", "notes")]
        if tid:
            db.execute("UPDATE tenants SET name=?, phone=?, email=?, notes=? WHERE id=?", (*vals, tid))
        else:
            tid = db.execute("INSERT INTO tenants (name, phone, email, notes) VALUES (?,?,?,?)", vals).lastrowid
        db.commit()
        nxt = request.args.get("next")
        return redirect(nxt if _local(nxt) else url_for("main.tenant_detail", tid=tid))
    return render_template("tenant_form.html", t=t)


@bp.route("/tenants/<int:tid>")
def tenant_detail(tid):
    db = get_db()
    t = db.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone() or abort(404)
    leases = db.execute("""SELECT l.*, p.name AS pname FROM leases l JOIN properties p ON p.id=l.property_id
                           WHERE tenant_id=? ORDER BY start_date DESC""", (tid,)).fetchall()
    docs = db.execute("SELECT * FROM documents WHERE tenant_id=? ORDER BY uploaded_at DESC", (tid,)).fetchall()
    paid = db.execute("""SELECT t.* FROM transactions t JOIN leases l ON l.id=t.lease_id WHERE l.tenant_id=?
                         ORDER BY t.date DESC LIMIT 24""", (tid,)).fetchall()
    return render_template("tenant.html", t=t, leases=leases, docs=docs, paid=paid,
                           doc_categories=DOC_CATEGORIES)


@bp.route("/tenants/<int:tid>/delete", methods=["POST"])
def tenant_delete(tid):
    get_db().execute("DELETE FROM tenants WHERE id=?", (tid,))
    get_db().commit()
    return redirect(url_for("main.tenants"))


@bp.route("/properties/<int:pid>/leases/new", methods=["POST"])
@bp.route("/leases/<int:lid>/edit", methods=["POST"])
def lease_save(pid=None, lid=None):
    db = get_db()
    if lid:
        pid = (db.execute("SELECT property_id FROM leases WHERE id=?", (lid,)).fetchone() or abort(404))[0]
    tenant_id = request.form.get("tenant_id")
    if tenant_id == "new" or not tenant_id:
        name = request.form.get("new_tenant_name", "").strip()
        if not name:
            flash("Choose a tenant or enter a new tenant name", "error")
            return redirect(url_for("main.property_detail", pid=pid, tab="tenants"))
        tenant_id = db.execute("INSERT INTO tenants (name, phone, email) VALUES (?,?,?)",
                               (name, request.form.get("new_tenant_phone") or None,
                                request.form.get("new_tenant_email") or None)).lastrowid
    vals = (int(tenant_id), request.form["start_date"], request.form.get("end_date") or None, _num("rent"),
            _num("deposit"), int(_num("payment_day", 1)), request.form.get("notes") or None)
    if lid:
        db.execute("""UPDATE leases SET tenant_id=?, start_date=?, end_date=?, rent=?, deposit=?, payment_day=?,
                      notes=? WHERE id=?""", (*vals, lid))
    else:
        db.execute("""INSERT INTO leases (tenant_id, start_date, end_date, rent, deposit, payment_day, notes,
                      property_id) VALUES (?,?,?,?,?,?,?,?)""", (*vals, pid))
        db.execute("UPDATE properties SET status='rented' WHERE id=? AND status IN ('vacant','renovating')", (pid,))
    db.commit()
    return redirect(url_for("main.property_detail", pid=pid, tab="tenants"))


@bp.route("/leases/<int:lid>/delete", methods=["POST"])
def lease_delete(lid):
    db = get_db()
    pid = (db.execute("SELECT property_id FROM leases WHERE id=?", (lid,)).fetchone() or abort(404))[0]
    db.execute("DELETE FROM leases WHERE id=?", (lid,))
    db.commit()
    return redirect(url_for("main.property_detail", pid=pid, tab="tenants"))


# --------------------------------------------------------------------------- documents

def _store_document(f, pid, tenant_id, tx_id, title, category, expires_on):
    original = f.filename
    stored = storage.save(f)
    get_db().execute("""INSERT INTO documents (property_id, tenant_id, transaction_id, title, category, filename,
                        original_name, expires_on) VALUES (?,?,?,?,?,?,?,?)""",
                     (pid, tenant_id, tx_id, title or original, category, stored, original, expires_on))


@bp.route("/documents", methods=["GET", "POST"])
def documents():
    db = get_db()
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a file", "error")
        else:
            pid = request.form.get("property_id") or None
            tenant = request.form.get("tenant_id") or None
            _store_document(f, pid, tenant, None, request.form.get("title"), request.form.get("category"),
                            request.form.get("expires_on") or None)
            db.commit()
        back = request.form.get("back")
        return redirect(back if _local(back) else url_for("main.documents"))
    docs = db.execute("""SELECT d.*, p.name AS pname, t.name AS tname FROM documents d
                         LEFT JOIN properties p ON p.id=d.property_id LEFT JOIN tenants t ON t.id=d.tenant_id
                         ORDER BY d.uploaded_at DESC""").fetchall()
    props = db.execute("SELECT id, name FROM properties ORDER BY name").fetchall()
    return render_template("documents.html", docs=docs, props=props, doc_categories=DOC_CATEGORIES)


@bp.route("/documents/<int:did>/file")
def document_file(did):
    d = get_db().execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone() or abort(404)
    return storage.send(d["filename"], d["original_name"] or "file",
                        as_attachment=request.args.get("download") == "1")


@bp.route("/documents/<int:did>/delete", methods=["POST"])
def document_delete(did):
    db = get_db()
    d = db.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone() or abort(404)
    storage.delete(d["filename"])
    db.execute("DELETE FROM documents WHERE id=?", (did,))
    db.commit()
    back = request.form.get("back")
    return redirect(back if _local(back) else url_for("main.documents"))


# --------------------------------------------------------------------------- valuations

@bp.route("/properties/<int:pid>/valuations", methods=["POST"])
def valuation_add(pid):
    get_db().execute("INSERT INTO valuations (property_id, date, value, source, notes) VALUES (?,?,?,?,?)",
                     (pid, request.form.get("date") or date.today().isoformat(), _num("value"),
                      request.form.get("source") or "manual", request.form.get("notes") or None))
    get_db().commit()
    return redirect(url_for("main.property_detail", pid=pid))


@bp.route("/valuations/<int:vid>/delete", methods=["POST"])
def valuation_delete(vid):
    db = get_db()
    pid = (db.execute("SELECT property_id FROM valuations WHERE id=?", (vid,)).fetchone() or abort(404))[0]
    db.execute("DELETE FROM valuations WHERE id=?", (vid,))
    db.commit()
    return redirect(url_for("main.property_detail", pid=pid))


# --------------------------------------------------------------------------- analytics

@bp.route("/analytics")
def analytics():
    db = get_db()
    today = date.today()
    bundles = svc.all_properties()
    pid = request.args.get("property", type=int)
    period = request.args.get("period", "month")
    sel = [b for b in bundles if not pid or b["p"]["id"] == pid]
    starts = [fin.to_date(b["p"]["purchase_date"]) for b in sel if b["p"]["purchase_date"]]
    first_tx = db.execute("SELECT min(date) FROM transactions").fetchone()[0]
    if first_tx:
        starts.append(fin.to_date(first_tx))
    start = min(starts) if starts else fin.add_months(today, -11)
    if period == "month" and not request.args.get("all"):
        start = max(start, fin.add_months(date(today.year, today.month, 1), -23))
    agg = {}
    cat_rows = []
    for b in sel:
        loan = fin.Loan.from_property(b["p"])
        for r in fin.monthly_table(b["txs"], loan, start, today):
            a = agg.setdefault(r["month"], {k: 0.0 for k in r if k != "month"})
            for k, v in r.items():
                if k != "month":
                    a[k] += v
        cat_rows += [t for t in b["txs"] if fin.to_date(t["date"]) >= start]
    monthly = [{"month": k, **v} for k, v in sorted(agg.items())]
    table = fin.yearly_table(monthly) if period == "year" else monthly
    for r in table:
        r["label"] = r.get("year") or r.get("month")
    cats = fin.category_totals([t for t in cat_rows if t["kind"] == "expense"])
    return render_template("analytics.html", bundles=bundles, pid=pid, period=period, table=table,
                           cats=cats, totals=svc.portfolio_totals(sel))


# --------------------------------------------------------------------------- market & settings

@bp.route("/market", methods=["GET", "POST"])
def market_view():
    db = get_db()
    if request.method == "POST":
        results = market.refresh_all(db)
        for name, ok, msg in results:
            flash(f"{name}: {msg}", "ok" if ok else "error")
        return redirect(url_for("main.market_view"))
    rate, why = svc.appreciation()
    return render_template("market.html", ctx=market.overview(db), rate=rate, why=why)


@bp.route("/settings", methods=["GET", "POST"])
def settings_view():
    db = get_db()
    if request.method == "POST":
        for k in settings(db):
            if k in request.form:
                set_setting(db, k, request.form[k].strip())
        db.commit()
        flash("Settings saved")
        return redirect(url_for("main.settings_view"))
    return render_template("settings.html", s=settings(db))


@bp.route("/market/import", methods=["POST"])
def market_import():
    db = get_db()
    try:
        f = request.files.get("file")
        if f and f.filename:
            msg = market.import_deals_bytes(db, f.read(), f.filename)
        else:
            msg = market.import_deals_url(db, request.form.get("url", ""))
        flash(msg)
    except Exception as e:
        flash(f"Import failed — {e}", "error")
    return redirect(url_for("main.market_view"))


@bp.route("/market/clear", methods=["POST"])
def market_clear():
    get_db().execute("DELETE FROM market_deals")
    get_db().commit()
    flash("Imported deals cleared")
    return redirect(url_for("main.market_view"))
