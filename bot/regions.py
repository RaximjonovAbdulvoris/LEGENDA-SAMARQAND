"""Regional routing. Missing destinations never fall back to another branch."""
from bot.config import ARCHIVE_GROUP, BRAND_GROUP, DRIVER_GROUPS
from bot.route_settings import destination

ANDIJON = "andijon"
TASHKENT = "tashkent"
REGION_NAMES = {TASHKENT: "Toshkent shahri", ANDIJON: "Andijon shahri"}


def get_region(context) -> str | None:
    region = context.user_data.get("region")
    return region if region in REGION_NAMES else None


def region_name(region: str) -> str:
    return REGION_NAMES[region]


def clear_application(context) -> None:
    region = get_region(context)
    context.user_data.clear()
    if region:
        context.user_data["region"] = region


def driver_groups(region: str) -> list[str]:
    if region == ANDIJON:
        return [value for i, fallback in enumerate(DRIVER_GROUPS, 1)
                if (value := destination(f"ANDIJON_DRIVER_GROUP_{i}", fallback))]
    if region == TASHKENT:
        return [value for i in range(1, 5)
                if (value := destination(f"TASHKENT_DRIVER_GROUP_{i}"))]
    return []


def application_group(region: str, kind: str) -> str:
    if kind != "brand":
        return ""
    if region == ANDIJON:
        return destination("ANDIJON_BRAND_GROUP", BRAND_GROUP)
    if region == TASHKENT:
        return destination("TASHKENT_BRAND_GROUP")
    return ""


def archive_group(region: str) -> str:
    if region == ANDIJON:
        return destination("ANDIJON_ARCHIVE_GROUP", ARCHIVE_GROUP)
    if region == TASHKENT:
        return destination("TASHKENT_ARCHIVE_GROUP")
    return ""
