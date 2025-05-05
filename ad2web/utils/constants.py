# ad2web/utils/constants.py

"""Application-wide constant definitions for validation and default values."""

# Form field length constraints
USERNAME_LEN_MIN: int = 4       # Minimum length for username
USERNAME_LEN_MAX: int = 25      # Maximum length for username

REALNAME_LEN_MIN: int = 4       # Minimum length for real name
REALNAME_LEN_MAX: int = 25      # Maximum length for real name

PASSWORD_LEN_MIN: int = 6       # Minimum length for passwords
PASSWORD_LEN_MAX: int = 64      # Maximum length for passwords

AGE_MIN: int = 1                # Minimum allowable age
AGE_MAX: int = 300              # Maximum allowable age

DEPOSIT_MIN: float = 0.00       # Minimum deposit amount
DEPOSIT_MAX: float = 9999999999.99  # Maximum deposit amount

# Default length for string fields (e.g., database columns)
STRING_LEN: int = 64

# Sex type codes and labels
MALE: int = 1
FEMALE: int = 2
OTHER: int = 9
SEX_TYPE: dict[int, str] = {
    MALE: u"Male",
    FEMALE: u"Female",
    OTHER: u"Other",
}
