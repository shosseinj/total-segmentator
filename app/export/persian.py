"""Persian display names; API/model identifiers remain unchanged."""
import re

NAMES={
    'spleen':'طحال','kidney':'کلیه','gallbladder':'کیسهٔ صفرا','liver':'کبد',
    'stomach':'معده','pancreas':'لوزالمعده','adrenal_gland':'غدهٔ فوق‌کلیوی',
    'esophagus':'مری','trachea':'نای','thyroid_gland':'غدهٔ تیروئید',
    'small_bowel':'رودهٔ باریک','duodenum':'دوازدهه','colon':'رودهٔ بزرگ',
    'urinary_bladder':'مثانه','prostate':'پروستات','kidney_cyst':'کیست کلیه',
    'sacrum':'استخوان خاجی','heart':'قلب','aorta':'آئورت','pulmonary_vein':'ورید ریوی',
    'brachiocephalic_trunk':'تنهٔ بازویی‌سری','subclavian_artery':'شریان زیرترقوه‌ای',
    'common_carotid_artery':'شریان کاروتید مشترک','brachiocephalic_vein':'ورید بازویی‌سری',
    'atrial_appendage':'زائدهٔ دهلیزی','superior_vena_cava':'ورید اجوف فوقانی',
    'inferior_vena_cava':'ورید اجوف تحتانی','portal_vein_and_splenic_vein':'ورید باب و ورید طحالی',
    'iliac_artery':'شریان ایلیاک','iliac_vena':'ورید ایلیاک','humerus':'استخوان بازو',
    'scapula':'استخوان کتف','clavicula':'استخوان ترقوه','femur':'استخوان ران',
    'hip':'استخوان لگن','spinal_cord':'نخاع','gluteus_maximus':'عضلهٔ سرینی بزرگ',
    'gluteus_medius':'عضلهٔ سرینی میانی','gluteus_minimus':'عضلهٔ سرینی کوچک',
    'autochthon':'عضلات عمقی پشت','iliopsoas':'عضلهٔ ایلیوپسواس',
    'brain':'مغز','skull':'جمجمه','sternum':'جناغ','costal_cartilages':'غضروف‌های دنده‌ای',
}


def persian_name(name):
    vertebra=re.fullmatch(r'vertebrae_([CTLS])(\d+)',name)
    if vertebra:
        area={'C':'گردنی','T':'سینه‌ای','L':'کمری','S':'خاجی'}[vertebra[1]]
        return f'مهرهٔ {area} {vertebra[2]}'
    rib=re.fullmatch(r'rib_(left|right)_(\d+)',name)
    if rib:return f'دندهٔ {"چپ" if rib[1]=="left" else "راست"} {rib[2]}'
    side=''
    for suffix,text in [('_left','چپ'),('_right','راست')]:
        if name.endswith(suffix):name=name[:-len(suffix)];side=' '+text;break
    lobes={'lung_upper_lobe':'لوب فوقانی ریه','lung_middle_lobe':'لوب میانی ریه','lung_lower_lobe':'لوب تحتانی ریه'}
    return lobes.get(name,NAMES.get(name,name.replace('_',' ')))+side
