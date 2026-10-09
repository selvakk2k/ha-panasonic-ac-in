"""Constants for the mirAIe integration."""
import re

DOMAIN = "miraie_in"
PACKAGE_NAME = "custom_components.miraie_in"
CONF_INSTALL_DATE = "install_date"

# 2.0 Hybrid Architecture Config Keys

CONF_CONTROL_PLANE = "control_plane"
CONF_MODEL_CODE = "model_code"
CONF_BLASTER_ENTITY_ID = "blaster_entity_id"
CONF_RECEIVER_ENTITY_ID = "receiver_entity_id"
CONF_ROOM_TEMP_SENSOR = "room_temp_sensor"
CONF_AVAILABILITY_ENTITY_ID = "availability_entity_id"
CONF_IR_FORMAT = "ir_format"
CONF_PRIMARY_BACKEND = "primary_backend"
CONF_HYBRID_SUBMODE = "hybrid_submode"
CONF_SWING_TYPE = "swing_type"
CONF_HAS_HEAT = "has_heat"
CONF_HAS_NANOE = "has_nanoe"
CONF_CONVERTI_TIER = "converti_tier"

CONTROL_PLANE_CLOUD = "cloud"
CONTROL_PLANE_IR = "ir"
CONTROL_PLANE_BOTH = "both"

BACKEND_CLOUD = "cloud"
BACKEND_IR = "ir"

HYBRID_SUBMODE_AUTO = "auto"
HYBRID_SUBMODE_MANUAL = "manual"

# Commands that are hardware toggles or non-idempotent and must never be retransmitted on blaster reconnect
NON_RESYNCABLE_IR_MODES = frozenset({"display"})


# Possible swing state codes
H0 = "H0"
H1 = "H1"
H2 = "H2"
H3 = "H3"
H4 = "H4"
H5 = "H5"

V0 = "V0"
V1 = "V1"
V2 = "V2"
V3 = "V3"
V4 = "V4"
V5 = "V5"

SWING_V_MAP = {0: V0, 1: V1, 2: V2, 3: V3, 4: V4, 5: V5}
SWING_H_MAP = {0: H0, 1: H1, 2: H2, 3: H3, 4: H4, 5: H5}

# Friendly Vane Position Constants
SWING_AUTO = "Auto Swing"

SWING_V_TOP = "Top"
SWING_V_HIGH_MID = "High-Mid"
SWING_V_MID = "Mid"
SWING_V_LOW_MID = "Low-Mid"
SWING_V_BOTTOM = "Bottom"

SWING_H_LEFT = "Left"
SWING_H_LEFT_CENTER = "Left-Center"
SWING_H_CENTER = "Center"
SWING_H_RIGHT_CENTER = "Right-Center"
SWING_H_RIGHT = "Right"

SWING_V_LIST = [SWING_AUTO, SWING_V_TOP, SWING_V_HIGH_MID, SWING_V_MID, SWING_V_LOW_MID, SWING_V_BOTTOM]
SWING_H_LIST = [SWING_AUTO, SWING_H_LEFT, SWING_H_LEFT_CENTER, SWING_H_CENTER, SWING_H_RIGHT_CENTER, SWING_H_RIGHT]

SWING_V_TO_CODE = {
    SWING_AUTO: V0,
    SWING_V_TOP: V1,
    SWING_V_HIGH_MID: V2,
    SWING_V_MID: V3,
    SWING_V_LOW_MID: V4,
    SWING_V_BOTTOM: V5,
    V0: V0,
    V1: V1,
    V2: V2,
    V3: V3,
    V4: V4,
    V5: V5,
}

SWING_CODE_TO_V_FRIENDLY = {
    V0: SWING_AUTO,
    V1: SWING_V_TOP,
    V2: SWING_V_HIGH_MID,
    V3: SWING_V_MID,
    V4: SWING_V_LOW_MID,
    V5: SWING_V_BOTTOM,
    0: SWING_AUTO,
    1: SWING_V_TOP,
    2: SWING_V_HIGH_MID,
    3: SWING_V_MID,
    4: SWING_V_LOW_MID,
    5: SWING_V_BOTTOM,
}

SWING_H_TO_CODE = {
    SWING_AUTO: H0,
    SWING_H_LEFT: H1,
    SWING_H_LEFT_CENTER: H2,
    SWING_H_CENTER: H3,
    SWING_H_RIGHT_CENTER: H4,
    SWING_H_RIGHT: H5,
    H0: H0,
    H1: H1,
    H2: H2,
    H3: H3,
    H4: H4,
    H5: H5,
}

SWING_CODE_TO_H_FRIENDLY = {
    H0: SWING_AUTO,
    H1: SWING_H_LEFT,
    H2: SWING_H_LEFT_CENTER,
    H3: SWING_H_CENTER,
    H4: SWING_H_RIGHT_CENTER,
    H5: SWING_H_RIGHT,
    0: SWING_AUTO,
    1: SWING_H_LEFT,
    2: SWING_H_LEFT_CENTER,
    3: SWING_H_CENTER,
    4: SWING_H_RIGHT_CENTER,
    5: SWING_H_RIGHT,
}

# Preset for Clean
PRESET_CLEAN = "clean"

# Preset for Converti (formatted as cv_NNN for custom card & HA translation engine compatibility)
PRESET_CONVERTI_C110 = "cv_110"
PRESET_CONVERTI_C100 = "cv_100"
PRESET_CONVERTI_C90 = "cv_90"
PRESET_CONVERTI_C80 = "cv_80"
PRESET_CONVERTI_C70 = "cv_70"
PRESET_CONVERTI_C60 = "cv_60"
PRESET_CONVERTI_C55 = "cv_55"
PRESET_CONVERTI_C50 = "cv_50"
PRESET_CONVERTI_C40 = "cv_40"

# Converti 7-in-1 capacity steps: cv_110/cv_100/cv_90/cv_80/cv_70/cv_55/cv_40.
CONVERTI_7IN1_PRESET_MODES = [
    PRESET_CONVERTI_C110,
    PRESET_CONVERTI_C100,
    PRESET_CONVERTI_C90,
    PRESET_CONVERTI_C80,
    PRESET_CONVERTI_C70,
    PRESET_CONVERTI_C55,
    PRESET_CONVERTI_C40,
]

# Converti 8-in-1 capacity steps: cv_110/cv_100/cv_90/cv_80/cv_70/cv_60/cv_50/cv_40.
CONVERTI_8IN1_PRESET_MODES = [
    PRESET_CONVERTI_C110,
    PRESET_CONVERTI_C100,
    PRESET_CONVERTI_C90,
    PRESET_CONVERTI_C80,
    PRESET_CONVERTI_C70,
    PRESET_CONVERTI_C60,
    PRESET_CONVERTI_C50,
    PRESET_CONVERTI_C40,
]

# --- Converti 7-in-1 vs 8-in-1 model support ---
#
# Verified directly against Panasonic's own store.in.panasonic.com
# /2025-model/ and /2026-model/ catalog pages (not third-party
# retailers/trackers, which were found to have inconsistent year
# labelling). Every model confirmed under the 2026 catalog is 8-in-1;
# every one still under 2025 is 7-in-1 -- but the generation letter
# that marks "2026" differs by series group:
#
#   Group A (NU, SU):      2025 = "A" (7-in-1)  ->  2026 = "B" (8-in-1)
#   Group B (EZ, HU, EU):  2025 = "B" (7-in-1)  ->  2026 = "C" (8-in-1)
#
# The generation letter is a per-series revision counter, not a
# fleet-wide year code -- it cannot be compared across series, only
# against its own group's threshold below. Only letters from the
# current (2024 onward) A/B/C cycle are recognised; older codes (e.g.
# "Z") are intentionally left unmapped and fall back to 7-in-1, since
# there's no evidence either way for that older generation and older
# models are out of scope for now.
_CONVERTI_LETTER_ORDER = {"A": 1, "B": 2, "C": 3}

CONVERTI_GROUP_A_SERIES = ("NU", "SU")
CONVERTI_GROUP_A_8IN1_THRESHOLD = "B"

CONVERTI_GROUP_B_SERIES = ("EZ", "HU", "EU")
CONVERTI_GROUP_B_8IN1_THRESHOLD = "C"

# Known gap: "QU" (e.g. CS-CU-QU26BKYFM) is a confirmed 7-in-1 model in
# the 2025 catalog, but not yet in either group above -- its 2026
# behaviour is unconfirmed. It currently falls through to the 7-in-1
# default, which is correct for the 2025 unit but unverified for any
# 2026 QU model. If you can confirm a 2026 QU model's Converti step
# count from an official Panasonic listing, please open an issue/PR.

# No confirmed exceptions to the pattern above at this time. An earlier
# version of this file listed CS-EU12BKY3FM as one, based on unverified
# early research -- Panasonic's own retailer listings (Croma, Amazon,
# and others) explicitly describe it as "7-in-1 Convertible", and it's
# correctly classified as 7-in-1 by the general rule below anyway (EU
# group threshold for 8-in-1 is "C"; EU12BKY3FM carries letter "B").
# If you find a real exception, please open an issue/PR with a link to
# an official Panasonic listing confirming it.
CONVERTI_8IN1_MODEL_EXCEPTIONS: set[str] = set()


def _matches_series(model_number: str, series: str) -> bool:
    """Check if model_number matches the series prefix followed by tonnage digits."""
    return bool(re.search(rf"(?:CS-|CU-|CS-CU-|^){re.escape(series)}\d{{2}}", model_number))


def _extract_generation_letter(model_number: str, series: str) -> str | None:
    """Pull the single generation-letter character that follows a
    known series prefix in a model number, e.g. "EU18CKY5XFM" with
    series "EU" -> "C" (the letter right after the tonnage digits).
    """
    match = re.search(rf"(?:CS-|CU-|CS-CU-|^){re.escape(series)}(\d+)([A-Z])", model_number)
    if match:
        return match.group(2)
    return None


# --- Heat mode ("Hot & Cold") model support ---
#
# Verified directly against Panasonic's own store.in.panasonic.com
# listings: EZ-series and KZ-series models are explicitly labelled
# "Hot & Cold" in their product titles/descriptions. Series such as
# NU, SU, and HU carry no such designation and are cooling-only.
# Unlike Converti gating, this isn't generation-letter-dependent --
# every EZ/KZ model found (2024 through 2026 catalogs) supports heat,
# so a simple series-prefix match is sufficient. If a cooling-only
# EZ/KZ variant or a heat-capable model outside these two series turns
# up, please open an issue/PR with a link to the official listing.
HEAT_CAPABLE_SERIES = ("EZ", "KZ")


def supports_heat_mode(model_number: str | None) -> bool:
    """Return whether a given model supports heat ("Hot & Cold") mode."""
    if not model_number or not isinstance(model_number, str):
        return False

    model_number = model_number.upper()
    return any(_matches_series(model_number, series) for series in HEAT_CAPABLE_SERIES)


def get_converti_preset_modes(model_number: str | None) -> list[str]:
    """Return the Converti preset list appropriate for a given model.

    See the comment block above CONVERTI_GROUP_A_SERIES for how this
    is derived. Falls back to the 7-in-1 preset set (the original
    stock behaviour) for anything unrecognised, so unknown or older
    models are never offered presets they can't actually use.
    """
    if not model_number or not isinstance(model_number, str):
        return CONVERTI_7IN1_PRESET_MODES

    model_number = model_number.upper()

    if model_number in CONVERTI_8IN1_MODEL_EXCEPTIONS:
        return CONVERTI_8IN1_PRESET_MODES

    for series_group, threshold in (
        (CONVERTI_GROUP_A_SERIES, CONVERTI_GROUP_A_8IN1_THRESHOLD),
        (CONVERTI_GROUP_B_SERIES, CONVERTI_GROUP_B_8IN1_THRESHOLD),
    ):
        for series in series_group:
            if not _matches_series(model_number, series):
                continue

            letter = _extract_generation_letter(model_number, series)
            if (
                letter in _CONVERTI_LETTER_ORDER
                and threshold in _CONVERTI_LETTER_ORDER
                and _CONVERTI_LETTER_ORDER[letter] >= _CONVERTI_LETTER_ORDER[threshold]
            ):
                return CONVERTI_8IN1_PRESET_MODES

    return CONVERTI_7IN1_PRESET_MODES


# --- Nanoe air purifier gating (Untested - no physical device to verify) ---
#
# nanoe-G and nanoe-X air purification technologies are available on premium
# series (primarily the XU series and HU Amaze Grey series) in the Panasonic
# India catalog.
NANOE_CAPABLE_SERIES = ("XU", "HU")


def supports_nanoe(model_number: str | None) -> bool:
    """Return whether a given model supports nanoe air purification."""
    if not model_number or not isinstance(model_number, str):
        return False

    model_number = model_number.upper()
    return any(_matches_series(model_number, series) for series in NANOE_CAPABLE_SERIES)


