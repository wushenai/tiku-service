import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import IO, Union, Dict

from fontTools.ttLib.tables._g_l_y_f import Glyph, table__g_l_y_f
from fontTools.ttLib.ttFont import TTFont

# 康熙部首替换表
KX_RADICALS_TAB = str.maketrans(
    # 康熙部首
    "⼀⼁⼂⼃⼄⼅⼆⼇⼈⼉⼊⼋⼌⼍⼎⼏⼐⼑⼒⼓⼔⼕⼖⼗⼘⼙⼚⼛⼜⼝⼞⼟⼠⼡⼢⼣⼤⼥⼦⼧⼨⼩⼪⼫⼬⼭⼮⼯⼰⼱⼲⼳⼴⼵⼶⼷⼸⼹⼺⼻⼼⼽⼾⼿⽀⽁⽂⽃⽄⽅⽆⽇⽈⽉⽊⽋⽌⽍⽎⽏⽐⽑⽒⽓⽔⽕⽖⽗⽘⽙⽚⽛⽜⽝⽞⽟⽠⽡⽢⽣⽤⽥⽦⽧⽨⽩⽪⽫⽬⽭⽮⽯⽰⽱⽲⽳⽴⽵⽶⽷⽸⽹⽺⽻⽼⽽⽾⽿⾀⾁⾂⾃⾄⾅⾆⾇⾈⾉⾊⾋⾌⾍⾎⾏⾐⾑⾒⾓⾔⾕⾖⾗⾘⾙⾚⾛⾜⾝⾞⾟⾠⾡⾢⾣⾤⾥⾦⾧⾨⾩⾪⾫⾬⾭⾮⾯⾰⾱⾲⾳⾴⾵⾶⾷⾸⾹⾺⾻⾼髙⾽⾾⾿⿀⿁⿂⿃⿄⿅⿆⿇⿈⿉⿊⿋⿌⿍⿎⿏⿐⿑⿒⿓⿔⿕⺠⻬⻩⻢⻜⻅⺟⻓",
    # 对应汉字
    "一丨丶丿乙亅二亠人儿入八冂冖冫几凵刀力勹匕匚匸十卜卩厂厶又口囗土士夂夊夕大女子宀寸小尢尸屮山巛工己巾干幺广廴廾弋弓彐彡彳心戈戶手支攴文斗斤方无日曰月木欠止歹殳毋比毛氏气水火爪父爻爿片牙牛犬玄玉瓜瓦甘生用田疋疒癶白皮皿目矛矢石示禸禾穴立竹米糸缶网羊羽老而耒耳聿肉臣自至臼舌舛舟艮色艸虍虫血行衣襾見角言谷豆豕豸貝赤走足身車辛辰辵邑酉采里金長門阜隶隹雨青非面革韋韭音頁風飛食首香馬骨高高髟鬥鬯鬲鬼魚鳥鹵鹿麥麻黃黍黑黹黽鼎鼓鼠鼻齊齒龍龜龠民齐黄马飞见母长",
)


class FontHashDAO:
    """原始字体hashmap DAO"""
    char_map: Dict[str, str]  # unicode -> hsah
    hash_map: Dict[str, str]  # hash -> unicode

    def __init__(self, file: str = None):
        p = Path(file) if file else self._default_path()
        with open(p, "r", encoding="utf-8") as fp:
            _map: dict = json.load(fp)
        self.char_map = _map
        self.hash_map = dict(zip(_map.values(), _map.keys()))

    @staticmethod
    def _default_path() -> Path:
        """font_map.json 的位置。

        源码运行：与本文件同目录。
        PyInstaller 打包：资源解包在 sys._MEIPASS，数据目录则在 exe 旁边，
        两处都找一下，避免冻结后直接抛 FileNotFoundError。
        """
        import sys
        if getattr(sys, "frozen", False):
            for base in (getattr(sys, "_MEIPASS", None),
                         Path(sys.executable).resolve().parent):
                if base:
                    p = Path(base) / "font_map.json"
                    if p.exists():
                        return p
        return Path(__file__).resolve().parent / "font_map.json"

    def find_char(self, font_hash: str) -> str:
        """以hash查内码"""
        return self.hash_map.get(font_hash)

    def find_hash(self, char: str) -> str:
        """以内码查hash"""
        return self.char_map.get(char)


# 懒加载：模块导入时不再读 1.5MB 的 font_map.json。
# 原实现在 import 阶段就实例化，冻结环境下路径不对会直接崩掉整个服务。
_fonthash_dao = None


def get_fonthash_dao() -> FontHashDAO:
    global _fonthash_dao
    if _fonthash_dao is None:
        _fonthash_dao = FontHashDAO()
    return _fonthash_dao


def hash_glyph(glyph: Glyph) -> str:
    """ttf字形曲线转hash算法实现"""
    pos_bin = ""
    last = 0
    for i in range(glyph.numberOfContours):
        for j in range(last, glyph.endPtsOfContours[i] + 1):
            pos_bin += f"{glyph.coordinates[j][0]}{glyph.coordinates[j][1]}{glyph.flags[j] & 0x01}"
        last = glyph.endPtsOfContours[i] + 1
    return hashlib.md5(pos_bin.encode()).hexdigest()


def font2map(file: Union[IO, Path, str]) -> Dict[str, str]:
    """以加密字体计算hashMap"""
    font_hashmap = {}
    if isinstance(file, str):
        file = BytesIO(base64.b64decode(file[47:]))
    with TTFont(file, lazy=False) as fontFile:
        table: table__g_l_y_f = fontFile["glyf"]
        for name in table.glyphOrder:
            font_hashmap[name] = hash_glyph(table.glyphs[name])
    return font_hashmap


def decrypt(dststr_fontmap: Dict[str, str], dst_str: str) -> str:
    """解码字体解密"""
    dao = get_fonthash_dao()
    ori_str = ""
    for char in dst_str:
        if dstchar_hash := dststr_fontmap.get(f"uni{ord(char):X}"):
            # 存在于“密钥”字体，解密
            orichar_hash = dao.find_char(dstchar_hash)
            if orichar_hash is not None:
                ori_str += chr(int(orichar_hash[3:], 16))
        else:
            # 不存在于“密钥”字体，直接复制
            ori_str += char
    # 替换解密后的康熙部首
    ori_str = ori_str.translate(KX_RADICALS_TAB)
    return ori_str
