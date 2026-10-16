"""User roles, mirroring the NestJS `UserType` enum.

Source of truth: src/user/dto/user.dto.ts (enum UserType). The numbers are
what NestJS puts in the JWT `user_type` claim, so they must never drift.
"""

from enum import IntEnum


class UserType(IntEnum):
    Admin = 0
    User = 1  # patient
    Doctor = 2
    Guest = 3
