
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


MerchantGateway = Literal[
    "razorpay",
    "cashfree",
    "payu",
]


class MerchantProfileUpdate(BaseModel):
    enabled_gateways: list[MerchantGateway] = Field(
        default_factory=list
    )

    preferred_gateway: MerchantGateway | None = None

    smart_routing_enabled: bool = False

    @model_validator(mode="after")
    def validate_gateway_preferences(self):
        self.enabled_gateways = list(
            dict.fromkeys(self.enabled_gateways)
        )

        if (
            self.preferred_gateway is not None
            and self.preferred_gateway not in self.enabled_gateways
        ):
            raise ValueError(
                "Preferred gateway must be included in enabled_gateways"
            )

        if (
            self.smart_routing_enabled
            and not self.enabled_gateways
        ):
            raise ValueError(
                "At least one gateway must be enabled for smart routing"
            )

        return self


class MerchantProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled_gateways: list[str]
    preferred_gateway: str | None
    smart_routing_enabled: bool
    is_active: bool
