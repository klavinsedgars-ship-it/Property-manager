def money(v, decimals=0):
    if v is None or v == "":
        return "—"
    v = float(v)
    s = f"{abs(v):,.{decimals}f}".replace(",", " ")
    return f"{'−' if v < 0 else ''}€{s}"


def pct(v, decimals=1):
    if v is None:
        return "—"
    return f"{v * 100:.{decimals}f}%"


def num(v, decimals=0):
    if v is None or v == "":
        return "—"
    return f"{float(v):,.{decimals}f}".replace(",", " ")


def register_filters(app):
    app.jinja_env.filters["money"] = money
    app.jinja_env.filters["pct"] = pct
    app.jinja_env.filters["num"] = num
