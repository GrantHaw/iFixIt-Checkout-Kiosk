from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
import db
from slack_notify import send_slack_alert
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "changeme-in-prod-obviously")

with app.app_context():
    db.init_db()


# =====================
#   PI-FACING API
# =====================

@app.route("/api/tap", methods=["POST"])
def api_tap():
    data = request.json or {}
    nfc_uid = data.get("nfc_uid", "").strip()
    if not nfc_uid:
        return jsonify({"error": "no nfc_uid"}), 400

    user = db.get_user_by_nfc(nfc_uid)
    if not user:
        return jsonify({"error": "unknown_card", "nfc_uid": nfc_uid}), 404

    conn = db.get_db()
    active = conn.execute(
        """SELECT k.id, k.name FROM kits k
           WHERE k.checked_out_by = ? AND k.status = 'checked_out'""",
        (user["id"],)
    ).fetchone()
    conn.close()

    return jsonify({
        "user": user,
        "has_checkout": dict(active) if active else None,
    })


@app.route("/api/users", methods=["GET"])
def api_users():
    """list all users for the senior's user picker"""
    users = db.list_users()
    return jsonify(users)


@app.route("/api/kits", methods=["GET"])
def api_kits():
    kits = db.list_kits()
    return jsonify(kits)


@app.route("/api/kit/<int:kit_id>/bits", methods=["GET"])
def api_kit_bits(kit_id):
    bits = db.get_kit_bits(kit_id)
    kit = db.get_kit(kit_id)
    if not kit:
        return jsonify({"error": "kit not found"}), 404
    return jsonify({"kit": kit, "bits": bits})


@app.route("/api/checkout", methods=["POST"])
def api_checkout():
    data = request.json or {}
    kit_id = data.get("kit_id")
    user_id = data.get("user_id")

    if not kit_id or not user_id:
        return jsonify({"error": "need kit_id and user_id"}), 400

    kit = db.get_kit(kit_id)
    if not kit:
        return jsonify({"error": "kit not found"}), 404
    if kit["status"] != "available":
        return jsonify({"error": "kit not available"}), 409

    db.checkout_kit(kit_id, user_id)
    return jsonify({"ok": True, "message": f"checked out {kit['name']}"})


@app.route("/api/return", methods=["POST"])
def api_return():
    data = request.json or {}
    kit_id = data.get("kit_id")
    user_id = data.get("user_id")
    missing_bits = data.get("missing_bits", [])

    if not kit_id or not user_id:
        return jsonify({"error": "need kit_id and user_id"}), 400

    kit = db.get_kit(kit_id)
    user = db.get_user(user_id)
    if not kit or not user:
        return jsonify({"error": "kit or user not found"}), 404

    result = db.return_kit(kit_id, user_id, missing_bits)

    if result["increased"]:
        send_slack_alert(
            kit_name=kit["name"],
            reporter_name=user["name"],
            reporter_slack=user.get("slack_id"),
            new_missing=result["new_missing"],
            old_missing=result["old_missing"],
            last_borrowers=result["last_borrowers"],
        )

    return jsonify({
        "ok": True,
        "old_missing": result["old_missing"],
        "new_missing": result["new_missing"],
        "alert_sent": result["increased"],
    })


# =====================
#   ADMIN DASHBOARD
# =====================

@app.route("/admin")
def admin_home():
    kits = db.list_kits()
    users = db.list_users()
    history = db.get_checkout_history(limit=20)
    return render_template("admin.html", kits=kits, users=users, history=history)


# -- user management --

@app.route("/admin/users/add", methods=["POST"])
def admin_add_user():
    name = request.form.get("name", "").strip()
    nfc = request.form.get("nfc_uid", "").strip() or None
    is_senior = bool(request.form.get("is_senior"))
    if name:
        db.create_user(name, nfc, None, is_senior)
        flash(f"added {name}", "success")
    return redirect(url_for("admin_home") + "#users")

@app.route("/admin/users/<int:uid>/edit", methods=["POST"])
def admin_edit_user(uid):
    name = request.form.get("name", "").strip()
    nfc = request.form.get("nfc_uid", "").strip() or None
    is_senior = bool(request.form.get("is_senior"))
    if name:
        db.update_user(uid, name, nfc, None, is_senior)
        flash(f"updated {name}", "success")
    return redirect(url_for("admin_home") + "#users")

@app.route("/admin/users/<int:uid>/toggle-senior", methods=["POST"])
def admin_toggle_senior(uid):
    db.toggle_senior(uid)
    flash("senior status updated", "success")
    return redirect(url_for("admin_home") + "#users")

@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
def admin_delete_user(uid):
    db.delete_user(uid)
    flash("user deleted", "success")
    return redirect(url_for("admin_home") + "#users")


# -- kit management --

@app.route("/admin/kits/<int:kid>")
def admin_kit_detail(kid):
    kit = db.get_kit(kid)
    if not kit:
        flash("kit not found", "error")
        return redirect(url_for("admin_home"))
    bits = db.get_kit_bits(kid)
    return render_template("kit_detail.html", kit=kit, bits=bits)

@app.route("/admin/kits/<int:kid>/bits/<int:bid>/toggle", methods=["POST"])
def admin_toggle_bit(kid, bid):
    conn = db.get_db()
    row = conn.execute("SELECT present FROM bits WHERE id = ? AND kit_id = ?", (bid, kid)).fetchone()
    if row:
        new_val = 0 if row["present"] else 1
        conn.execute("UPDATE bits SET present = ? WHERE id = ?", (new_val, bid))
        conn.commit()
    conn.close()
    return redirect(url_for("admin_kit_detail", kid=kid))

@app.route("/admin/kits/add", methods=["POST"])
def admin_add_kit():
    name = request.form.get("name", "").strip()
    if name:
        kit_id = db.create_kit(name, db.MAKO_BITS)
        flash(f"created {name} with {len(db.MAKO_BITS)} bits", "success")
    return redirect(url_for("admin_home") + "#kits")

@app.route("/admin/kits/<int:kid>/delete", methods=["POST"])
def admin_delete_kit(kid):
    db.delete_kit(kid)
    flash("kit deleted", "success")
    return redirect(url_for("admin_home") + "#kits")


# -- config --

@app.route("/admin/config", methods=["POST"])
def admin_config():
    webhook = request.form.get("slack_webhook_url", "").strip()
    channel = request.form.get("slack_channel", "").strip()
    enabled = "true" if request.form.get("slack_enabled") else "false"

    db.set_config("slack_webhook_url", webhook)
    db.set_config("slack_channel", channel)
    db.set_config("slack_enabled", enabled)
    flash("config saved", "success")
    return redirect(url_for("admin_home") + "#config")


# =====================
#   KIOSK SERVING
# =====================

@app.route("/")
def kiosk_home():
    return render_template("kiosk.html")


# =====================
#   STARTUP
# =====================

if __name__ == "__main__":
    db.init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
