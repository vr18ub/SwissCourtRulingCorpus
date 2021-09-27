import json
import re
import unicodedata
from collections import OrderedDict

import pandas as pd

from scrc.utils.log_utils import get_logger


def save_to_path(content, path, overwrite=False):
    """
    Create the parent directories of they do not exist.
    If file does not exist already, save content to path.
    :param content:     content to be saved
    :param path:        path of file to be saved
    :param overwrite:   if True overwrites the current content at that path
    :return:
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = get_logger()
    # check if
    if path.exists():
        logger.debug(f"Path {path} exists already")
        if overwrite:
            logger.debug("Overwrite option specified")
        else:
            return

    logger.debug(f"Saving file to {path}")
    # actually do the saving
    if isinstance(content, bytes):
        path.write_bytes(content)
    elif isinstance(content, str):
        path.write_text(content)
    elif isinstance(content, dict):
        path.write_text(json.dumps(content))
    else:
        raise ValueError(f"Invalid data type {type(content)} supplied.")


def get_raw_text(html) -> str:
    """
    Add the entire text: harder for doing sentence splitting later because of header and footer
    :param html:
    :return:
    """

    raw_text = html.get_text()
    return raw_text


def clean_text(text: str) -> str:
    """
    Clean text from nasty tokens
    :param text:    the text to be cleaned
    :return:
    """
    cleaned_text = text
    # https://stackoverflow.com/questions/16467479/normalizing-unicode
    cleaned_text = unicodedata.normalize('NFKC', cleaned_text)  # normalize strings
    cleaned_text = re.sub('(\w+)-\n+(\w+)', '\1\2', cleaned_text)  # remove hyphens before new line
    cleaned_text = re.sub(r"\u00a0", ' ', cleaned_text)  # replace NBSP with normal whitespace
    cleaned_text = re.sub(r"\xa0", ' ', cleaned_text)  # replace \xa0 with normal whitespace
    cleaned_text = re.sub(r"\x00", '', cleaned_text)  # remove \x00 completely
    cleaned_text = re.sub(r"\s+", ' ', cleaned_text)  # replace all whitespace with a single whitespace
    cleaned_text = re.sub(r"_+", '_', cleaned_text)  # remove duplicate underscores (from anonymisations)
    cleaned_text = cleaned_text.strip()  # remove leading and trailing whitespace
    cleaned_text = "".join(
        ch for ch in cleaned_text if unicodedata.category(ch)[0] != "C")  # remove control characters
    return cleaned_text


def chunker(iterable, chunk_size):
    return (iterable[pos: pos + chunk_size] for pos in range(0, len(iterable), chunk_size))


def get_file_gen(path):
    def get_path(path, chunk):
        return path / f"part.{chunk}.parquet"

    chunk = 0
    file = get_path(path, chunk)
    while file.exists():
        yield chunk, pd.read_parquet(file)
        chunk += 1
        file = get_path(path, chunk)
    return None


def string_contains_one_of_list(string: str, lst: list):
    """
    If the string contains an item in the list,
     we return that item (which can also be used as a truth value in if conditions)
     and otherwise we return False
     """
    for item in lst:
        if item in string:
            return item
    return False


def int_to_roman(num: int) -> str:
    """
    Converts an integer to a roman numeral string
    :param num: the input number
    :return:    the output roman numeral string
    """
    roman = OrderedDict()
    roman[1000] = "M"
    roman[900] = "CM"
    roman[500] = "D"
    roman[400] = "CD"
    roman[100] = "C"
    roman[90] = "XC"
    roman[50] = "L"
    roman[40] = "XL"
    roman[10] = "X"
    roman[9] = "IX"
    roman[5] = "V"
    roman[4] = "IV"
    roman[1] = "I"

    def roman_num(num):
        for r in roman.keys():
            x, y = divmod(num, r)
            yield roman[r] * x
            num -= (r * x)
            if num <= 0:
                break

    return "".join([a for a in roman_num(num)])

if __name__ == '__main__':
    logger = get_logger()
    pd.set_option('display.max_rows', 500)
    pd.set_option('display.max_columns', 500)
    pd.set_option('display.width', 1000)