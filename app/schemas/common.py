from enum import Enum


class ProductLine(str, Enum):
    car = "car"


class CoverageLevel(str, Enum):
    basic = "basic"
    standard = "standard"
    premium = "premium"


class UsageType(str, Enum):
    private = "private"
    commute = "commute"
    business = "business"
