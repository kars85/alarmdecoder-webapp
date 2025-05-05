# ad2web/utils/string_utils.py

"""String processing utilities (e.g., random ID generation)."""

import random
import string


def id_generator(size: int = 10, chars: str = string.ascii_letters + string.digits) -> str:
    """Generate a random string of the given size using the specified characters.

    Default size is 10 characters, using uppercase/lowercase letters and digits.
    """
    # Note: This is not cryptographically secure; it is intended for generic ID generation.
    return ''.join(random.choice(chars) for _ in range(size))
