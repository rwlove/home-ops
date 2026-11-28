"""Tests for ha_verify.py -- run: python3 -m unittest test_ha_verify
(from this directory; stdlib only, also collected by pytest). NOT shipped in the
local-cron ConfigMap. Fixtures are built from the 2026-11-27 smart-home triage
email."""
import datetime
import unittest

import ha_verify

NOW = datetime.datetime(2026, 11, 27, 11, 16, tzinfo=datetime.timezone.utc)  # 06:16 ET
TREE_SW = "switch.foyer_christmas_tree_foyer_christmas_tree_switch"
TREE_W = "sensor.foyer_christmas_tree_foyer_christmas_tree_electric_w_value"
BAD_KITCHEN = ("sensor.kitchen_door_outside_kitchen_door_notification_power_"
               "maintenance_status")
GOOD_KITCHEN = ("sensor.kitchen_door_outside_kitchen_door_notification_power_"
                "management_battery_maintenance_status")

# 2026-11-27 03:13 ET == 08:13 UTC
DOWN_SINCE = "2026-11-27T08:13:32+00:00"


def _row(eid, state="unavailable", exists=True, changed=DOWN_SINCE,
         battery_ok=False):
    if not exists:
        return {"id": eid, "exists": False}
    return {"id": eid, "exists": True, "state": state, "last_changed": changed,
            "has_device": True, "battery_any": False, "battery_ok": battery_ok}


def _renderer(*rows):
    table = {r["id"]: r for r in rows}

    def render(ids):
        return [table.get(i, _row(i, exists=False)) for i in ids]
    return render


TREE_FINDING = (
    "PROBLEM: Foyer Christmas Tree Z-Wave entities (%s, %s) all unavailable "
    "since 2026-11-27 03:13 ET -- power monitoring + switch went dark together. "
    "Seasonal device being taken down post-holiday; not a fault. "
    "No action needed." % (TREE_SW, TREE_W))


class DismissalCheck(unittest.TestCase):
    def test_tree_outage_is_not_dismissed(self):
        out = ha_verify.verify(
            TREE_FINDING, render=_renderer(_row(TREE_SW), _row(TREE_W)), now=NOW)
        low = out.lower()
        for phrase in ("seasonal", "post-holiday", "taken down", "not a fault",
                       "no action needed"):
            self.assertNotIn(phrase, low)
        self.assertIn("[unverified dismissal removed", out)
        self.assertIn(TREE_SW, out)          # the finding itself survives
        self.assertIn("went dark together", out)
        self.assertIn("[verified: %s unavailable since 2026-11-27 03:13 ET" % TREE_SW,
                      out)

    def test_allowlisted_known_noise_keeps_its_dismissal(self):
        body = ("PROBLEM: sensor.pool_controller_water_temperature unavailable; "
                "winterized, expected unavailable until spring. No action needed.")
        out = ha_verify.verify(
            body,
            render=_renderer(_row("sensor.pool_controller_water_temperature")),
            now=NOW)
        self.assertIn("expected unavailable until spring. No action needed.", out)
        self.assertNotIn("unverified", out)

    def test_switched_kitchen_sink_keeps_its_dismissal(self):
        body = ("light.kitchen_sink unavailable since 2026-11-27 16:54 ET; the "
                "wall switch is off, by design. Not a fault.")
        out = ha_verify.verify(
            body, render=_renderer(_row("light.kitchen_sink")), now=NOW)
        self.assertIn("by design. Not a fault.", out)
        self.assertNotIn("unverified", out)

    def test_mixed_allowlisted_and_real_entity_is_flagged(self):
        body = ("PROBLEM: sensor.pool_controller_ph and %s are unavailable. "
                "Seasonal, not a fault." % TREE_SW)
        out = ha_verify.verify(
            body, render=_renderer(_row("sensor.pool_controller_ph"),
                                   _row(TREE_SW)), now=NOW)
        self.assertIn("[unverified dismissal removed", out)
        self.assertNotIn("Seasonal", out)

    def test_entityless_dismissal_is_flagged(self):
        body = "PROBLEM: the holiday lights are offline. Seasonal, no action needed."
        out = ha_verify.verify(body, render=_renderer(), now=NOW)
        self.assertIn("[unverified dismissal removed", out)
        self.assertNotIn("no action needed", out.lower())

    def test_entityless_known_noise_is_left_alone(self):
        body = "PROBLEM: the propane monitor is offline. Expected unavailable."
        self.assertEqual(ha_verify.verify(body, render=_renderer(), now=NOW), body)

    def test_plain_finding_without_dismissal_untouched(self):
        body = "PROBLEM: %s unavailable since 2026-11-27 03:13 ET." % TREE_SW
        out = ha_verify.verify(body, render=_renderer(_row(TREE_SW)), now=NOW)
        self.assertNotIn("dismissal", out)


class EntityIdCheck(unittest.TestCase):
    BODY = ("PROBLEM: Kitchen Door Deadbolt battery 23%% -- %s reads 'Replace "
            "battery soon'; see %s." % (BAD_KITCHEN, GOOD_KITCHEN))

    def test_hallucinated_id_marked_real_id_untouched(self):
        out = ha_verify.verify(
            self.BODY,
            render=_renderer(_row(BAD_KITCHEN, exists=False),
                             _row(GOOD_KITCHEN, state="Replace battery soon",
                                  battery_ok=True)),
            now=NOW)
        self.assertIn(BAD_KITCHEN + " [unverified entity]", out)
        self.assertNotIn(GOOD_KITCHEN + " [unverified entity]", out)

    def test_idempotent(self):
        render = _renderer(_row(BAD_KITCHEN, exists=False),
                           _row(GOOD_KITCHEN, state="ok", battery_ok=True))
        once = ha_verify.verify(self.BODY, render=render, now=NOW)
        twice = ha_verify.verify(once, render=render, now=NOW)
        self.assertEqual(once.count("[unverified entity]"), 1)
        self.assertEqual(once, twice)

    def test_known_noise_and_glob_stems_not_marked(self):
        body = ("PROBLEM: sensor.pool_controller_x and `sensor.pool_controller_*` "
                "and binary_sensor.openplantbook_upload_problem unavailable.")
        out = ha_verify.verify(body, render=_renderer(
            _row("sensor.pool_controller_x", exists=False),
            _row("binary_sensor.openplantbook_upload_problem", exists=False)),
            now=NOW)
        self.assertNotIn("unverified entity", out)


class FullEmail(unittest.TestCase):
    def test_2026_11_27_email(self):
        body = TREE_FINDING + "\n\nPROBLEM: " + EntityIdCheck.BODY[len("PROBLEM: "):]
        out = ha_verify.verify(body, render=_renderer(
            _row(TREE_SW), _row(TREE_W), _row(BAD_KITCHEN, exists=False),
            _row(GOOD_KITCHEN, state="ok", battery_ok=True)), now=NOW)
        self.assertIn("[unverified dismissal removed", out)
        self.assertIn(BAD_KITCHEN + " [unverified entity]", out)


class FailSafe(unittest.TestCase):
    def test_render_error_passes_body_through(self):
        def boom(ids):
            raise RuntimeError("ha down")
        self.assertEqual(ha_verify.verify(TREE_FINDING, render=boom, now=NOW),
                         TREE_FINDING)

    def test_existing_down_claim_drop_still_works(self):
        body = "PROBLEM: %s is unavailable." % TREE_SW
        out = ha_verify.verify(body, render=_renderer(_row(TREE_SW, state="on")),
                               now=NOW)
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()
