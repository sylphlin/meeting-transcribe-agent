"""
scripts/alignment_engine.py - Multilingual LCS Alignment and Timestamp Projection Engine.
Aligns Track A unchunked global speaker diarization words with Track B chunked word timestamps.
Supports CJK character-aware and Western word-level sequence matching, repetition loop suppression,
and LLM semantic paragraph timestamp re-projection.
"""

from difflib import SequenceMatcher
import re
from typing import Any, Dict, List, Optional, Tuple
import unicodedata


_SENTENCE_END_RE = re.compile(r'[.!?。！？]+["\'”’）\)]*$')
_CLAUSE_END_RE = re.compile(r'[,;:，；：、]+["\'”’）\)]*$')


def _is_cjk_char(ch: str) -> bool:
    """Return True if the character belongs to CJK Ideographs, Kana, or Hangul blocks."""
    code = ord(ch)
    return (
        0x3040 <= code <= 0x30FF  # Hiragana and Katakana
        or 0x3400 <= code <= 0x4DBF  # CJK Unified Ideographs Extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
        or 0xAC00 <= code <= 0xD7AF  # Hangul Syllables
        or 0xF900 <= code <= 0xFAFF  # CJK Compatibility Ideographs
        or 0x3100 <= code <= 0x312F  # Bopomofo
    )


_SIMP_TO_TRAD_SRC = (
    "万与丑专业丛东丝丢两严丧个丰临为丽举么义乌乐乔习乡书买乱争于亏云亘亚产亩亲亵亸亿仅仆从仑仓仪们价众优会伛伞伟传伣伤伥伦伧伪伫体佣"
    "佥侠侣侥侦侧侨侩侪侬俣俦俨俩俪俫俭债倾偬偻偾偿傥傧储傩儿兑兖党兰关兴兹养兽冁内冈册写军农冯冲决况冻净凄凉减凑凛几凤凫凭凯击凿刍刘"
    "则刚创删别刬刭刹刽刿剀剂剐剑剥剧劝办务劢动励劲劳势勋勚匀匦匮区医华协单卖占卢卤卧卫却厂厅历厉压厌厍厐厕厘厢厣厦厨厩厮县叁参叆叇双"
    "发变叙叠叶号叹叽后吓吕吗吣吨听启吴呐呒呓呕呖呗员呙呛呜咏咙咛咝咤响哑哒哓哔哕哗哙哜哝哟唛唝唠唡唢唤啧啬啭啮啰啴啸喂喷喽喾嗫嗳嘘嘤"
    "嘱噜嚣团园囱围囵国图圆圣圹场坂坏块坚坛坜坝坞坟坠垄垅垆垒垦垩垫垭垯垱垲垴埘埙埚埯堑堕塆墙壮声壳壶壸处备复够头夸夹夺奁奂奋奖奥妆妇"
    "妈妩妪妫姗姹娄娅娆娇娈娱娲娴婳婴婵婶媪媭嫒嫔嫱嬷孙学孪宁宝实宠审宪宫宽宾寝对寻导寿将尔尘尝尧尴尸尽层屃屉届属屡屦屿岁岂岖岗岘岙岚"
    "岛岭岽岿峃峄峡峣峤峥峦崂崃崄崭嵘嵚嵝巅巩巯币帅师帏帐帘帜带帧帮帱帻帼幂干并广庄庆庐庑库应庙庞废庼廪开异弃弑张弥弪弯弹强归当录彟彦"
    "彷彻征径徕忆忏忧忾怀态怂怃怄怅怆怜总怼怿恋恒恳恶恸恹恺恻恼恽悦悫悬悭悮悯惊惧惨惩惫惬惭惮惯愠愤愦愿慑慭懑懒懔戆戋戏戗战戬戯户扑执"
    "扩扪扫扬扰抚抛抟抠抡抢护报担拟拢拣拥拦拧拨择挂挚挛挜挝挞挟挠挡挢挣挤挥挦挽捝捞损捡换捣据掳掴掷掸掺掼揽揾揿搀搁搂搅携摄摅摆摇摈摊"
    "撄撑撵撷撸撺擞攒敌敛敩数斋斓斗斩断无旧时旷旸昙昵昼昽显晋晒晓晔晕晖暂暧术朴机杀杂权杆杠条来杨杩杰极构枞枢枣枥枧枨枪枫枭柜柠柽栀栅"
    "标栈栉栊栋栌栎栏树栖样栾桠桡桢档桤桥桦桧桨桩梦梼梾梿检棁棂棱椁椟椠椤椭楼榄榅榇榈榉槚槛槟槠横樯樱橥橱橹橼檩欢欤欧歼殁殇残殒殓殚殡"
    "殴毁毂毕毙毡毵氇气氢氩氲汇汉汤汹沉沟没沣沤沥沦沧沨沩沪泄泞泪泶泷泸泺泻泼泽泾洁洒洼浃浅浆浇浈浉浊测浍济浏浐浑浒浓浔浕涂涌涛涝涞涟"
    "涠涡涢涣涤润涧涨涩淀渊渌渍渎渐渑渔渖渗温湾湿溁溃溅溆溇滗滚滞滟滠满滢滤滥滦滨滩滪漓漤潆潇潋潍潜潴澛澜濑濒灏灭灯灵灾灿炀炉炖炜炝点"
    "炼炽烁烂烃烛烟烦烧烨烩烫烬热焕焖焘煴爱爷牍牦牵牺犊状犷犸犹狈狝狞独狭狮狯狰狱狲猃猎猕猡猪猫猬献獭玑玙玚玛玮环现玱玺珐珑珰珲琎琏琐"
    "琼瑶瑷璎瓒瓮瓯电画畅畴疖疗疟疠疡疬疭疮疯疱疴痈痉痒痖痨痪痫瘅瘆瘗瘘瘪瘫瘾瘿癞癣癫皑皱皲盏盐监盖盗盘眍眦眬着睁睐睑睾瞆瞒瞩矫矶矾矿"
    "砀码砖砗砚砜砺砻砾础硁硕硖硗硙硚确硷碍碛碜碱礴礼祃祎祢祯祷祸禀禄禅离秃秆种积称秽秾稆税稣稳穑穷窃窍窎窑窜窝窥窦窭竖竞笃笋笔笕笺笼"
    "笾筑筚筛筜筝筹筼签简箓箦箧箨箩箪箫篑篓篮篯篱簖籁籴类籼粜粝粤粪粮糁糇紧絷纟纠纡红纣纤纥约级纨纩纪纫纬纭纮纯纰纱纲纳纴纵纶纷纸纹纺"
    "纻纼纽纾线绀绁绂练组绅细织终绉绊绋绌绍绎经绐绑绒结绔绕绖绗绘给绚绛络绝绞统绠绡绢绣绤绥绦继绨绩绪绫绬续绮绯绰绱绲绳维绵绶绷绸绹绺"
    "绻综绽绾绿缀缁缂缃缄缅缆缇缈缉缊缋缌缍缎缏缐缑缒缓缔缕编缗缘缙缚缛缜缝缞缟缠缡缢缣缤缥缦缧缨缩缪缫缬缭缮缯缰缱缲缳缴缵罂网罗罚罢"
    "罴羁羟羡翘翙翚耢耧耸耻聂聋职聍联聩聪肃肠肤肮肾肿胀胁胆胜胧胨胪胫胶脉脍脏脐脑脓脔脚脱脶脸腊腌腘腭腻腽腾膑膻臜舆舍舣舰舱舻艰艳艺节"
    "芈芗芜芦苁苇苈苋苌苍苎苏苧苹范茎茏茑茔茕茧荆荐荙荚荛荜荞荟荠荡荣荤荥荦荧荨荩荪荫荬荭荮药莅莱莲莳莴莶获莸莹莺莼萚萝萤营萦萧萨葱蒇"
    "蒉蒋蒌蓝蓟蓠蓣蓥蓦蔂蔷蔹蔺蔼蕰蕲蕴薮藓蘖虏虑虚虫虬虮虱虽虾虿蚀蚁蚂蚕蚝蚬蛊蛎蛏蛮蛰蛱蛲蛳蛴蜕蜗蜡蝇蝈蝉蝎蝼蝾螀螨蟏衅衔补衬衮袄袅"
    "袆袜袭袯装裆裈裢裣裤裥褛褴襕见观觃规觅视觇览觉觊觋觌觍觎觏觐觑觞触觯訚詟誉誊讠计订讣认讥讦讧讨让讪讫讬训议讯记讱讲讳讴讵讶讷许讹"
    "论讻讼讽设访诀证诂诃评诅识诇诈诉诊诋诌词诎诏诐译诒诓诔试诖诗诘诙诚诛诜话诞诟诠诡询诣诤该详诧诨诩诪诫诬语诮误诰诱诲诳说诵诶请诸诹"
    "诺读诼诽课诿谀谁谂调谄谅谆谇谈谊谋谌谍谎谏谐谑谒谓谔谕谖谗谘谙谚谛谜谝谞谟谠谡谢谣谤谥谦谧谨谩谪谫谬谭谮谯谰谱谲谳谴谵谶豮贝贞负"
    "贠贡财责贤败账货质贩贪贫贬购贮贯贰贱贲贳贴贵贶贷贸费贺贻贼贽贾贿赀赁赂赃资赅赆赇赈赉赊赋赌赍赎赏赐赑赒赓赔赕赖赗赘赙赚赛赜赝赞赟"
    "赠赡赢赣赪赵赶趋趱趸跃跄跞践跶跷跸跹跻踊踌踪踬踯蹑蹒蹰蹿躏躜躯车轧轨轩轪轫转轭轮软轰轱轲轳轴轵轶轷轸轹轺轻轼载轾轿辀辁辂较辄辅辆"
    "辇辈辉辊辋辌辍辎辏辐辑辒输辔辕辖辗辘辙辚辞辩辫边辽达迁过迈运还这进远违连迟迩迳迹适选逊递逦逻遗遥邓邝邬邮邹邺邻郏郐郑郓郦郧郸酂酝"
    "酦酱酽酾酿采释鉴銮錾钅钆钇针钉钊钋钌钍钎钏钐钑钒钓钔钕钖钗钘钙钚钛钜钝钞钟钠钡钢钣钤钥钦钧钨钩钪钫钬钭钮钯钰钱钲钳钴钵钶钷钸钹钺"
    "钻钼钽钾钿铀铁铂铃铄铅铆铇铈铉铊铋铌铍铎铏铐铑铒铓铔铕铖铗铘铙铚铛铜铝铞铟铠铡铢铣铤铥铦铧铨铩铪铫铬铭铮铯铰铱铲铳铴铵银铷铸铹铺"
    "铻铼铽链铿销锁锂锃锄锅锆锇锈锉锊锋锌锍锎锏锐锑锒锓锔锕锖锗锘错锚锛锜锝锞锟锠锡锢锣锤锥锦锧锨锩锪锫锬锭键锯锰锱锲锳锴锵锶锷锸锹锺"
    "锻锼锽锾锿镀镁镂镃镄镅镆镇镈镉镊镋镌镍镎镏镐镑镒镓镔镕镖镗镘镙镚镛镜镝镞镟镠镡镢镣镤镥镦镧镨镩镪镫镬镭镮镯镰镱镲镳镴镵镶长门闩闪"
    "闫闬闭问闯闰闱闲闳间闵闶闷闸闹闺闻闼闽闾闿阀阁阂阃阄阅阆阇阈阉阊阋阌阍阎阏阐阑阒阓阔阕阖阗阘阙阚阛队阳阴阵阶际陆陇陈陉陕陧陨险随"
    "隐隶隽难雏雠雳雾霁霡霭靓静靥鞑鞒鞯韦韧韨韩韪韫韬韵页顶顷顸项顺须顼顽顾顿颀颁颂颃预颅领颇颈颉颊颋颌颍颎颏颐频颒颓颔颕颖颗题颙颚颛"
    "颜额颞颟颠颡颢颤颥颦颧风飏飐飑飒飓飔飕飖飗飘飙飚飞飨餍饣饤饥饦饧饨饩饪饫饬饭饮饯饰饱饲饳饴饵饶饷饸饹饺饻饼饽饾饿馀馁馂馃馄馅馆馇"
    "馈馉馊馋馌馍馎馏馐馑馒馓馔馕马驭驮驯驰驱驲驳驴驵驶驷驸驹驺驻驼驽驾驿骀骁骂骃骄骅骆骇骈骉骊骋验骍骎骏骐骑骒骓骔骕骖骗骘骙骚骛骜骝"
    "骞骟骠骡骢骣骤骥骦骧髅髋髌鬓鬶魇魉鱼鱽鱾鱿鲀鲁鲂鲃鲄鲅鲆鲇鲈鲉鲊鲋鲌鲍鲎鲏鲐鲑鲒鲓鲔鲕鲖鲗鲘鲙鲚鲛鲜鲝鲞鲟鲠鲡鲢鲣鲤鲥鲦鲧鲨鲩鲪"
    "鲫鲬鲭鲮鲯鲰鲱鲲鲳鲴鲵鲶鲷鲸鲹鲺鲻鲼鲽鲾鲿鳀鳁鳂鳃鳄鳅鳆鳇鳈鳉鳊鳋鳌鳍鳎鳏鳐鳑鳒鳓鳔鳕鳖鳗鳘鳙鳚鳛鳜鳝鳞鳟鳠鳡鳢鳣鳤鸟鸠鸡鸢鸣鸤"
    "鸥鸦鸧鸨鸩鸪鸫鸬鸭鸮鸯鸰鸱鸲鸳鸴鸵鸶鸷鸸鸹鸺鸻鸼鸽鸾鸿鹀鹁鹂鹃鹄鹅鹆鹇鹈鹉鹊鹋鹌鹍鹎鹏鹐鹑鹒鹓鹔鹕鹖鹗鹘鹙鹚鹛鹜鹝鹞鹟鹠鹡鹢鹣鹤"
    "鹥鹦鹧鹨鹩鹪鹫鹬鹭鹮鹯鹰鹱鹲鹳鹴鹾麦麸麹黄黉黡黩黪黾鼋鼍鼗鼹齐齑齿龀龁龂龃龄龅龆龇龈龉龊龋龌龙龚龛龟鿎鿏鿔鿭"
)
_SIMP_TO_TRAD_DST = (
    "萬與醜專業叢東絲丟兩嚴喪個豐臨為麗舉麼義烏樂喬習鄉書買亂爭於虧雲亙亞產畝親褻嚲億僅僕從侖倉儀們價眾優會傴傘偉傳俔傷倀倫傖偽佇體傭"
    "僉俠侶僥偵側僑儈儕儂俁儔儼倆儷倈儉債傾傯僂僨償儻儐儲儺兒兌兗黨蘭關興茲養獸囅內岡冊寫軍農馮衝決況凍淨淒涼減湊凜幾鳳鳧憑凱擊鑿芻劉"
    "則剛創刪別剗剄剎劊劌剴劑剮劍剝劇勸辦務勱動勵勁勞勢勳勩勻匭匱區醫華協單賣佔盧鹵臥衛卻廠廳歷厲壓厭厙龎廁釐廂厴廈廚廄廝縣叄參靉靆雙"
    "發變敘疊葉號嘆嘰後嚇呂嗎唚噸聽啓吳吶嘸囈嘔嚦唄員咼嗆嗚詠嚨嚀噝吒響啞噠嘵嗶噦嘩噲嚌噥喲嘜嗊嘮啢嗩喚嘖嗇囀嚙囉嘽嘯餵噴嘍嚳囁噯噓嚶"
    "囑嚕囂團園囪圍圇國圖圓聖壙場阪壞塊堅壇壢壩塢墳墜壟壠壚壘墾堊墊埡墶壋塏堖塒塤堝垵塹墮壪牆壯聲殼壺壼處備復夠頭誇夾奪奩奐奮獎奧妝婦"
    "媽嫵嫗媯姍奼婁婭嬈嬌孌娛媧嫻嫿嬰嬋嬸媼嬃嬡嬪嬙嬤孫學孿寧寶實寵審憲宮寬賓寢對尋導壽將爾塵嘗堯尷屍盡層屓屜屆屬屢屨嶼歲豈嶇崗峴嶴嵐"
    "島嶺崬巋嶨嶧峽嶢嶠崢巒嶗崍嶮嶄嶸嶔嶁巔鞏巰幣帥師幃帳簾幟帶幀幫幬幘幗冪乾並廣莊慶廬廡庫應廟龐廢廎廩開異棄弒張彌弳彎彈強歸當錄彠彥"
    "徬徹徵徑徠憶懺憂愾懷態慫憮慪悵愴憐總懟懌戀恆懇惡慟懨愷惻惱惲悅愨懸慳悞憫驚懼慘懲憊愜慚憚慣慍憤憒願懾憖懣懶懍戇戔戲戧戰戩戱戶撲執"
    "擴捫掃揚擾撫拋摶摳掄搶護報擔擬攏揀擁攔擰撥擇掛摯攣掗撾撻挾撓擋撟掙擠揮撏輓挩撈損撿換搗據擄摑擲撣摻摜攬搵撳攙擱摟攪攜攝攄擺搖擯攤"
    "攖撐攆擷擼攛擻攢敵斂斆數齋斕鬥斬斷無舊時曠暘曇暱晝曨顯晉曬曉曄暈暉暫曖術樸機殺雜權桿槓條來楊榪傑極構樅樞棗櫪梘棖槍楓梟櫃檸檉梔柵"
    "標棧櫛櫳棟櫨櫟欄樹棲樣欒椏橈楨檔榿橋樺檜槳樁夢檮棶槤檢梲櫺稜槨櫝槧欏橢樓欖榲櫬櫚櫸檟檻檳櫧橫檣櫻櫫櫥櫓櫞檁歡歟歐殲歿殤殘殞殮殫殯"
    "毆毀轂畢斃氈毿氌氣氫氬氳匯漢湯洶沈溝沒灃漚瀝淪滄渢溈滬洩濘淚澩瀧瀘濼瀉潑澤涇潔灑窪浹淺漿澆湞溮濁測澮濟瀏滻渾滸濃潯濜塗湧濤澇淶漣"
    "潿渦溳渙滌潤澗漲澀澱淵淥漬瀆漸澠漁瀋滲溫灣濕濚潰濺漵漊潷滾滯灧灄滿瀅濾濫灤濱灘澦灕灠瀠瀟瀲濰潛瀦瀂瀾瀨瀕灝滅燈靈災燦煬爐燉煒熗點"
    "煉熾爍爛烴燭煙煩燒燁燴燙燼熱煥燜燾熅愛爺牘氂牽犧犢狀獷獁猶狽獮獰獨狹獅獪猙獄猻獫獵獼玀豬貓蝟獻獺璣璵瑒瑪瑋環現瑲璽琺瓏璫琿璡璉瑣"
    "瓊瑤璦瓔瓚甕甌電畫暢疇癤療瘧癘瘍癧瘲瘡瘋皰痾癰痙癢瘂癆瘓癇癉瘮瘞瘻癟癱癮癭癩癬癲皚皺皸盞鹽監蓋盜盤瞘眥矓著睜睞瞼睪瞶瞞矚矯磯礬礦"
    "碭碼磚硨硯碸礪礱礫礎硜碩硤磽磑礄確礆礙磧磣鹼礡禮禡禕禰禎禱禍稟祿禪離禿稈種積稱穢穠穭稅穌穩穡窮竊竅窵窯竄窩窺竇窶竪競篤筍筆筧箋籠"
    "籩築篳篩簹箏籌篔簽簡籙簀篋籜籮簞簫簣簍籃籛籬籪籟糴類秈糶糲粵糞糧糝餱緊縶糹糾紆紅紂纖紇約級紈纊紀紉緯紜紘純紕紗綱納紝縱綸紛紙紋紡"
    "紵紖紐紓線紺紲紱練組紳細織終縐絆紼絀紹繹經紿綁絨結絝繞絰絎繪給絢絳絡絕絞統綆綃絹繡綌綏縧繼綈績緒綾緓續綺緋綽緔緄繩維綿綬繃綢綯綹"
    "綣綜綻綰綠綴緇緙緗緘緬纜緹緲緝縕繢緦綞緞緶線緱縋緩締縷編緡緣縉縛縟縝縫縗縞纏縭縊縑繽縹縵縲纓縮繆繅纈繚繕繒繮繾繰繯繳纘罌網羅罰罷"
    "羆羈羥羨翹翽翬耮耬聳恥聶聾職聹聯聵聰肅腸膚骯腎腫脹脅膽勝朧腖臚脛膠脈膾髒臍腦膿臠腳脫腡臉臘醃膕齶膩膃騰臏羶臢輿捨艤艦艙艫艱艷藝節"
    "羋薌蕪蘆蓯葦藶莧萇蒼苧蘇薴蘋範莖蘢蔦塋煢繭荊薦薘莢蕘蓽蕎薈薺蕩榮葷滎犖熒蕁藎蓀蔭蕒葒葤藥蒞萊蓮蒔萵薟獲蕕瑩鶯蒓蘀蘿螢營縈蕭薩蔥蕆"
    "蕢蔣蔞藍薊蘺蕷鎣驀虆薔蘞藺藹薀蘄蘊藪蘚櫱虜慮虛蟲虯蟣蝨雖蝦蠆蝕蟻螞蠶蠔蜆蠱蠣蟶蠻蟄蛺蟯螄蠐蛻蝸蠟蠅蟈蟬蠍螻蠑螿蟎蠨釁銜補襯袞襖裊"
    "褘襪襲襏裝襠褌褳襝褲襇褸襤襴見觀覎規覓視覘覽覺覬覡覿覥覦覯覲覷觴觸觶誾讋譽謄訁計訂訃認譏訐訌討讓訕訖託訓議訊記訒講諱謳詎訝訥許訛"
    "論訩訟諷設訪訣證詁訶評詛識詗詐訴診詆謅詞詘詔詖譯詒誆誄試詿詩詰詼誠誅詵話誕詬詮詭詢詣諍該詳詫諢詡譸誡誣語誚誤誥誘誨誑說誦誒請諸諏"
    "諾讀諑誹課諉諛誰諗調諂諒諄誶談誼謀諶諜謊諫諧謔謁謂諤諭諼讒諮諳諺諦謎諞諝謨讜謖謝謠謗謚謙謐謹謾謫謭謬譚譖譙讕譜譎讞譴譫讖豶貝貞負"
    "貟貢財責賢敗賬貨質販貪貧貶購貯貫貳賤賁貰貼貴貺貸貿費賀貽賊贄賈賄貲賃賂贓資賅贐賕賑賚賒賦賭賫贖賞賜贔賙賡賠賧賴賵贅賻賺賽賾贋贊贇"
    "贈贍贏贛赬趙趕趨趲躉躍蹌躒踐躂蹺蹕躚躋踴躊蹤躓躑躡蹣躕躥躪躦軀車軋軌軒軑軔轉軛輪軟轟軲軻轤軸軹軼軤軫轢軺輕軾載輊轎輈輇輅較輒輔輛"
    "輦輩輝輥輞輬輟輜輳輻輯轀輸轡轅轄輾轆轍轔辭辯辮邊遼達遷過邁運還這進遠違連遲邇逕跡適選遜遞邐邏遺遙鄧鄺鄔郵鄒鄴鄰郟鄶鄭鄆酈鄖鄲酇醖"
    "醱醬釅釃釀採釋鑒鑾鏨釒釓釔針釘釗釙釕釷釺釧釤鈒釩釣鍆釹鍚釵鈃鈣鈈鈦鉅鈍鈔鐘鈉鋇鋼鈑鈐鑰欽鈞鎢鈎鈧鈁鈥鈄鈕鈀鈺錢鉦鉗鈷鉢鈳鉕鈽鈸鉞"
    "鑽鉬鉭鉀鈿鈾鐵鉑鈴鑠鉛鉚鉋鈰鉉鉈鉍鈮鈹鐸鉶銬銠鉺鋩錏銪鋮鋏鋣鐃銍鐺銅鋁銱銦鎧鍘銖銑鋌銩銛鏵銓鎩鉿銚鉻銘錚銫鉸銥鏟銃鐋銨銀銣鑄鐒鋪"
    "鋙錸鋱鏈鏗銷鎖鋰鋥鋤鍋鋯鋨鏽銼鋝鋒鋅鋶鐦鐧銳銻鋃鋟鋦錒錆鍺鍩錯錨錛錡鍀錁錕錩錫錮鑼錘錐錦鑕鍁錈鍃錇錟錠鍵鋸錳錙鍥鍈鍇鏘鍶鍔鍤鍬鍾"
    "鍛鎪鍠鍰鎄鍍鎂鏤鎡鐨鎇鏌鎮鎛鎘鑷鎲鐫鎳鎿鎦鎬鎊鎰鎵鑌鎔鏢鏜鏝鏍鏰鏞鏡鏑鏃鏇鏐鐔鐝鐐鏷鑥鐓鑭鐠鑹鏹鐙鑊鐳鐶鐲鐮鐿鑔鑣鑞鑱鑲長門閂閃"
    "閆閈閉問闖閏闈閒閎間閔閌悶閘鬧閨聞闥閩閭闓閥閣閡閫鬮閱閬闍閾閹閶鬩閿閽閻閼闡闌闃闠闊闋闔闐闒闕闞闤隊陽陰陣階際陸隴陳陘陝隉隕險隨"
    "隱隸雋難雛讎靂霧霽霢靄靚靜靨韃鞽韉韋韌韍韓韙韞韜韻頁頂頃頇項順須頊頑顧頓頎頒頌頏預顱領頗頸頡頰頲頜潁熲頦頤頻頮頹頷頴穎顆題顒顎顓"
    "顏額顳顢顛顙顥顫顬顰顴風颺颭颮颯颶颸颼颻飀飄飆飈飛饗饜飠飣飢飥餳飩餼飪飫飭飯飲餞飾飽飼飿飴餌饒餉餄餎餃餏餅餑餖餓餘餒餕餜餛餡館餷"
    "饋餶餿饞饁饃餺餾饈饉饅饊饌饢馬馭馱馴馳驅馹駁驢駔駛駟駙駒騶駐駝駑駕驛駘驍罵駰驕驊駱駭駢驫驪騁驗騂駸駿騏騎騍騅騌驌驂騙騭騤騷騖驁騮"
    "騫騸驃騾驄驏驟驥驦驤髏髖髕鬢鬹魘魎魚魛魢魷魨魯魴䰾魺鮁鮃鮎鱸鮋鮓鮒鮊鮑鱟鮍鮐鮭鮚鮳鮪鮞鮦鰂鮜鱠鱭鮫鮮鮺鮝鱘鯁鱺鰱鰹鯉鰣鰷鯀鯊鯇鮶"
    "鯽鯒鯖鯪鯕鯫鯡鯤鯧鯝鯢鯰鯛鯨鰺鯴鯔鱝鰈鰏鱨鯷鰮鰃鰓鰐鰍鰒鰉鰁鱂鯿鰠鰲鰭鰨鰥鰩鰟鰜鰳鰾鱈鱉鰻鰵鱅䲁鰼鱖鱔鱗鱒鱯鱤鱧鱣䲘鳥鳩雞鳶鳴鳲"
    "鷗鴉鶬鴇鴆鴣鶇鸕鴨鴞鴦鴒鴟鴝鴛鷽鴕鷥鷙鴯鴰鵂鴴鵃鴿鸞鴻鵐鵓鸝鵑鵠鵝鵒鷳鵜鵡鵲鶓鵪鵾鵯鵬鵮鶉鶊鵷鷫鶘鶡鶚鶻鶖鷀鶥鶩鷊鷂鶲鶹鶺鷁鶼鶴"
    "鷖鸚鷓鷚鷯鷦鷲鷸鷺䴉鸇鷹鸌鸏鸛鸘鹺麥麩麴黃黌黶黷黲黽黿鼉鞀鼴齊齏齒齔齕齗齟齡齙齠齜齦齬齪齲齷龍龔龕龜䃮䥑鎶鉨"
)
_SIMP_TO_TRAD_TABLE = str.maketrans(_SIMP_TO_TRAD_SRC, _SIMP_TO_TRAD_DST)


def convert_simplified_to_traditional(text: str) -> str:
    """Convert Simplified Chinese characters to Traditional Chinese before sequence alignment."""
    if not text:
        return ""
    return str(text).translate(_SIMP_TO_TRAD_TABLE)


def normalize_multilingual_token(text: str) -> str:
    """
    Standardize a multilingual token for sequence alignment.
    Strip all Unicode punctuation and symbols, convert to lowercase,
    and preserve CJK characters, Kana, Hangul, and alphanumeric text.
    """
    if not text:
        return ""
    cleaned = "".join(
        ch.lower()
        for ch in str(text)
        if not unicodedata.category(ch).startswith(("P", "S"))
    )
    return cleaned.strip()


def normalize_speaker_label(raw_label: Any) -> str:
    """Normalize a raw diarization speaker tag into 'Speaker N' format."""
    if raw_label is None:
        return "Speaker 1"
    s = str(raw_label).strip()
    if not s or s.lower() == "unknown":
        return "Speaker 1"
    m = re.match(r"^(?:speaker[\s_-]*|spk[\s_:-]*)?(\d+|[a-zA-Z])$", s, re.IGNORECASE)
    if m:
        return f"Speaker {m.group(1)}"
    return s


def join_multilingual_words(words: List[str]) -> str:
    """
    Join word tokens into natural text.
    Omit artificial spaces between adjacent CJK characters while keeping spaces
    between Western words and around CJK-to-Western boundaries as appropriate.
    """
    if not words:
        return ""
    out: List[str] = []
    for token in words:
        tok = str(token).strip()
        if not tok:
            continue
        if not out:
            out.append(tok)
            continue
        prev = out[-1]
        prev_last = prev[-1]
        curr_first = tok[0]
        # Attach without space when both sides are CJK or CJK punctuation
        if (
            (_is_cjk_char(prev_last) and _is_cjk_char(curr_first))
            or (_is_cjk_char(prev_last) and curr_first in "，。！？；：、）」』】》")
            or (prev_last in "，。！？；：、（）「」『』【】《》" and _is_cjk_char(curr_first))
            or (not _is_cjk_char(prev_last) and curr_first in ",.!?;:)]}")
        ):
            out.append(tok)
        else:
            out.append(" " + tok)
    return "".join(out).strip()


def deduplicate_overlap_micro_words(
    chunk_word_lists: List[Tuple[float, float, List[Dict[str, Any]]]]
) -> List[Dict[str, Any]]:
    """
    Merge micro word lists from overlapping audio chunks into a monotonic stream.
    Each tuple is (chunk_start_sec, chunk_end_sec, micro_words_with_local_offsets).
    When consecutive chunks overlap by a window (e.g. 5 seconds), split ownership
    at the midpoint of the overlap window to prevent duplicate words and time regression.
    """
    if not chunk_word_lists:
        return []

    num_chunks = len(chunk_word_lists)
    merged: List[Dict[str, Any]] = []

    for idx, (c_start, c_end, words) in enumerate(chunk_word_lists):
        min_allowed_sec = 0.0
        max_allowed_sec = float("inf")

        if idx > 0:
            prev_start, prev_end, _ = chunk_word_lists[idx - 1]
            if prev_end > c_start:
                min_allowed_sec = (c_start + prev_end) / 2.0
            else:
                min_allowed_sec = c_start

        if idx < num_chunks - 1:
            next_start, _, _ = chunk_word_lists[idx + 1]
            if c_end > next_start:
                max_allowed_sec = (next_start + c_end) / 2.0

        for w in words:
            st_raw = w.get("startOffset", w.get("start"))
            et_raw = w.get("endOffset", w.get("end"))
            st_local = AlignmentEngine.parse_offset_static(st_raw)
            et_local = AlignmentEngine.parse_offset_static(et_raw)
            st_global = st_local + c_start
            et_global = max(st_global, et_local + c_start)
            center_sec = (st_global + et_global) / 2.0

            if idx > 0 and center_sec < min_allowed_sec:
                continue
            if idx < num_chunks - 1 and center_sec >= max_allowed_sec:
                continue

            merged.append({
                "word": w.get("word", ""),
                "startOffset": f"{st_global:.3f}s",
                "endOffset": f"{et_global:.3f}s",
                "start": st_global,
                "end": et_global,
            })

    return merged


class AlignmentEngine:
    """
    Aligns Track A unchunked speaker-diarized word streams with Track B word timestamps.
    """

    def __init__(self, segment_base_seconds: float = 0.0):
        self.base_sec = float(segment_base_seconds)

    @staticmethod
    def parse_offset_static(offset_val: Any) -> float:
        """Parse an offset string ('12.340s') or numeric value into float seconds."""
        if offset_val is None:
            return 0.0
        if isinstance(offset_val, (int, float)):
            return float(offset_val)
        s = str(offset_val).strip().rstrip("s")
        if not s:
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    def _parse_offset(self, offset_str: Optional[str]) -> float:
        return self.parse_offset_static(offset_str)

    def suppress_repetition_loops(
        self,
        words: List[Dict[str, Any]],
        max_repeats: int = 3,
        max_ngram_len: int = 8,
    ) -> List[Dict[str, Any]]:
        """
        Suppress generative decoding repetition loops (for example, 'I don't know how to'
        repeated 15 times at music or noise transitions) while keeping normal speech.
        """
        if len(words) < 4:
            return list(words)

        norm_tokens = [normalize_multilingual_token(w.get("word", "")) for w in words]
        n_total = len(words)
        keep_indices: List[int] = []
        i = 0

        while i < n_total:
            loop_found = False
            # Check n-gram sizes from largest to smallest
            for ngram_size in range(min(max_ngram_len, (n_total - i) // 2), 0, -1):
                pattern = norm_tokens[i : i + ngram_size]
                if not any(pattern):
                    continue
                repeat_count = 1
                pos = i + ngram_size
                while pos + ngram_size <= n_total and norm_tokens[pos : pos + ngram_size] == pattern:
                    repeat_count += 1
                    pos += ngram_size

                # Multi-word phrases (>= 3 tokens) repeating >= 3 times are loops;
                # Short 1-2 token phrases repeating > max_repeats (e.g. >= 4 times) are loops.
                threshold = 3 if ngram_size >= 3 else (max_repeats + 1)
                if repeat_count >= threshold:
                    # Keep only the first occurrence of the repeated phrase
                    for k in range(i, i + ngram_size):
                        keep_indices.append(k)
                    i = pos
                    loop_found = True
                    break

            if not loop_found:
                keep_indices.append(i)
                i += 1

        return [words[idx] for idx in keep_indices]

    def _build_alignment_units(
        self,
        words: List[Dict[str, Any]],
        is_micro: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Expand word list into alignment units.
        Western words stay as single tokens; CJK tokens expand into individual characters
        with linearly interpolated sub-timestamps so differing CJK segmentation between
        Track A and Track B still aligns with 100% accuracy.
        """
        units: List[Dict[str, Any]] = []
        for w_idx, w in enumerate(words):
            raw_text = str(w.get("word", ""))
            norm = normalize_multilingual_token(raw_text)
            if not norm:
                continue

            st: Optional[float] = None
            et: Optional[float] = None
            if is_micro:
                st_raw = w.get("startOffset", w.get("start"))
                et_raw = w.get("endOffset", w.get("end"))
                if st_raw is not None:
                    st = self._parse_offset(st_raw) + self.base_sec
                if et_raw is not None:
                    et = self._parse_offset(et_raw) + self.base_sec
                if st is not None and et is None:
                    et = st
                elif et is not None and st is None:
                    st = et

            has_cjk = any(_is_cjk_char(ch) for ch in norm)
            if has_cjk and len(norm) > 1:
                # Split into CJK characters and contiguous non-CJK sub-tokens
                sub_tokens = re.findall(
                    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\u3100-\u312f]|[^\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\u3100-\u312f\s]+",
                    norm,
                )
                n_sub = max(1, len(sub_tokens))
                dur = (et - st) if (st is not None and et is not None and et >= st) else 0.0
                cjk_ord = 0
                for s_i, sub_tok in enumerate(sub_tokens):
                    sub_st = (st + (dur * s_i / n_sub)) if st is not None else None
                    sub_et = (st + (dur * (s_i + 1) / n_sub)) if st is not None else None
                    is_single_cjk = len(sub_tok) == 1 and _is_cjk_char(sub_tok)
                    cur_cjk_ord = cjk_ord if is_single_cjk else None
                    if is_single_cjk:
                        cjk_ord += 1
                    units.append({
                        "token": sub_tok,
                        "word_idx": w_idx,
                        "cjk_order": cur_cjk_ord,
                        "start": sub_st,
                        "end": sub_et,
                    })
            else:
                is_single_cjk = len(norm) == 1 and _is_cjk_char(norm)
                units.append({
                    "token": norm,
                    "word_idx": w_idx,
                    "cjk_order": 0 if is_single_cjk else None,
                    "start": st,
                    "end": et,
                })
        return units

    def project_timestamps(
        self,
        macro_words: List[Dict[str, Any]],
        micro_words: List[Dict[str, Any]],
        prefer_micro_cjk: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Project physical word timestamps from Track B (micro_words) onto Track A (macro_words).
        When prefer_micro_cjk is True (for example, when Track A uses cmn-Hans-CN for speaker
        diarization and Track B uses cmn-Hant-TW for Traditional Chinese word timestamps),
        first convert Track A words from Simplified to Traditional Chinese before alignment,
        and then project Track B's CJK character glyphs onto Track A words.
        """
        if not macro_words:
            return []

        for w in macro_words:
            w.setdefault("start", None)
            w.setdefault("end", None)
            if prefer_micro_cjk and w.get("word"):
                w["word"] = convert_simplified_to_traditional(str(w["word"]))

        if not micro_words:
            return macro_words

        macro_units = self._build_alignment_units(macro_words, is_micro=False)
        micro_units = self._build_alignment_units(micro_words, is_micro=True)

        norm_macro = [u["token"] for u in macro_units]
        norm_micro = [u["token"] for u in micro_units]

        matcher = SequenceMatcher(None, norm_macro, norm_micro, autojunk=False)
        paired_indices: List[Tuple[int, int]] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    paired_indices.append((i1 + k, j1 + k))
            elif tag == "replace":
                len_a = i2 - i1
                len_b = j2 - j1
                if len_a > 0 and len_b > 0:
                    all_cjk_a = all(any(_is_cjk_char(c) for c in macro_units[idx]["token"]) for idx in range(i1, i2))
                    all_cjk_b = all(any(_is_cjk_char(c) for c in micro_units[idx]["token"]) for idx in range(j1, j2))
                    if all_cjk_a and all_cjk_b and abs(len_a - len_b) <= max(2, int(0.4 * max(len_a, len_b))):
                        for k in range(len_a):
                            j_mapped = j1 + (
                                k if len_a == len_b else min(len_b - 1, int(round(k * (len_b - 1) / max(1, len_a - 1))))
                            )
                            paired_indices.append((i1 + k, j_mapped))

        cjk_replacements: Dict[int, Dict[int, str]] = {}

        for a_idx, b_idx in paired_indices:
            u_macro = macro_units[a_idx]
            u_micro = micro_units[b_idx]
            w_idx = u_macro["word_idx"]
            st = u_micro["start"]
            et = u_micro["end"]
            if st is not None:
                cur_st = macro_words[w_idx].get("start")
                if cur_st is None or st < cur_st:
                    macro_words[w_idx]["start"] = st
            if et is not None:
                cur_et = macro_words[w_idx].get("end")
                if cur_et is None or et > cur_et:
                    macro_words[w_idx]["end"] = et

            if (
                prefer_micro_cjk
                and u_macro.get("cjk_order") is not None
                and len(u_micro["token"]) == 1
                and _is_cjk_char(u_micro["token"])
            ):
                cjk_replacements.setdefault(w_idx, {})[u_macro["cjk_order"]] = u_micro["token"]

        if prefer_micro_cjk and cjk_replacements:
            for w_idx, repl_map in cjk_replacements.items():
                raw_w = str(macro_words[w_idx].get("word", ""))
                chars = list(raw_w)
                cjk_pos = 0
                for c_i, ch in enumerate(chars):
                    if _is_cjk_char(ch):
                        if cjk_pos in repl_map:
                            chars[c_i] = repl_map[cjk_pos]
                        cjk_pos += 1
                macro_words[w_idx]["word"] = "".join(chars)

        micro_starts = [u["start"] for u in micro_units if u.get("start") is not None]
        acoustic_start_sec = min(micro_starts) if micro_starts else 0.0

        return self._interpolate_word_timestamps(
            macro_words,
            acoustic_start_sec=acoustic_start_sec,
        )

    def _interpolate_word_timestamps(
        self,
        words: List[Dict[str, Any]],
        acoustic_start_sec: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Interpolate missing timestamps on unaligned words using anchored neighbors.
        For leading unaligned words (i == 0), anchor the start bound to the physical
        acoustic onset from Track B (acoustic_start_sec) so leading silence is not consumed.
        """
        n = len(words)
        if n == 0:
            return words

        # Fill missing start or end if one side exists on the same word
        for w in words:
            if w.get("start") is not None and w.get("end") is None:
                w["end"] = w["start"] + 0.15
            elif w.get("end") is not None and w.get("start") is None:
                w["start"] = max(0.0, w["end"] - 0.15)

        # Find contiguous spans of unaligned words
        i = 0
        while i < n:
            if words[i].get("start") is not None:
                i += 1
                continue
            j = i
            while j < n and words[j].get("start") is None:
                j += 1

            span_count = j - i
            if i == 0:
                onset_floor = max(0.0, float(acoustic_start_sec)) if acoustic_start_sec is not None else 0.0
                if j < n and words[j].get("start") is not None:
                    next_start = words[j]["start"]
                    if acoustic_start_sec is not None and onset_floor < next_start:
                        prev_end = onset_floor
                    else:
                        prev_end = max(onset_floor, next_start - (span_count * 0.25))
                else:
                    prev_end = onset_floor
                    next_start = prev_end + span_count * 0.25
            else:
                prev_end = words[i - 1]["end"] if words[i - 1].get("end") is not None else 0.0
                next_start = words[j]["start"] if j < n and words[j].get("start") is not None else prev_end + span_count * 0.25

            if next_start < prev_end:
                next_start = prev_end

            step = (next_start - prev_end) / span_count if span_count > 0 else 0.0
            for k in range(span_count):
                w_st = prev_end + k * step
                w_et = prev_end + (k + 1) * step
                words[i + k]["start"] = round(w_st, 3)
                words[i + k]["end"] = round(w_et, 3)

            i = j

        return words

    def aggregate_turns(
        self,
        aligned_words: List[Dict[str, Any]],
        soft_max_duration_sec: float = 65.0,
        hard_max_duration_sec: float = 110.0,
        pause_split_sec: float = 0.65,
    ) -> List[Dict[str, Any]]:
        """
        Aggregate aligned words into speaker turns.
        Automatically splits long single-speaker monologues at natural sentence and pause
        boundaries so interactive player cards remain readable and seekable.
        """
        turns: List[Dict[str, Any]] = []
        cur_turn: Optional[Dict[str, Any]] = None

        for idx, w in enumerate(aligned_words):
            raw_spk = w.get("speakerLabel", w.get("speaker", "Speaker 1"))
            spk = normalize_speaker_label(raw_spk)
            word_text = str(w.get("word", "")).strip()
            if not word_text:
                continue
            st = w.get("start")
            et = w.get("end")

            should_start_new = False
            if cur_turn is None or cur_turn["speaker"] != spk:
                should_start_new = True
            elif (
                cur_turn["start"] is not None
                and cur_turn["end"] is not None
                and soft_max_duration_sec > 0
            ):
                turn_dur = cur_turn["end"] - cur_turn["start"]
                prev_word_text = cur_turn["words"][-1] if cur_turn["words"] else ""
                gap_sec = (st - cur_turn["end"]) if (st is not None and cur_turn["end"] is not None) else 0.0

                is_sentence_end = bool(_SENTENCE_END_RE.search(prev_word_text))
                is_clause_end = bool(_CLAUSE_END_RE.search(prev_word_text))

                if turn_dur >= soft_max_duration_sec and is_sentence_end and gap_sec >= pause_split_sec:
                    should_start_new = True
                elif turn_dur >= (soft_max_duration_sec + 20.0) and is_sentence_end:
                    should_start_new = True
                elif turn_dur >= hard_max_duration_sec and (is_sentence_end or is_clause_end or gap_sec >= pause_split_sec):
                    should_start_new = True

            if should_start_new:
                if cur_turn:
                    cur_turn["text"] = join_multilingual_words(cur_turn["words"])
                    turns.append(cur_turn)
                cur_turn = {
                    "speaker": spk,
                    "words": [word_text],
                    "word_items": [{"word": word_text, "start": st, "end": et}],
                    "start": st,
                    "end": et,
                }
            else:
                cur_turn["words"].append(word_text)
                cur_turn["word_items"].append({"word": word_text, "start": st, "end": et})
                if st is not None and cur_turn["start"] is None:
                    cur_turn["start"] = st
                if et is not None:
                    cur_turn["end"] = et

        if cur_turn:
            cur_turn["text"] = join_multilingual_words(cur_turn["words"])
            turns.append(cur_turn)

        return self._smooth_turn_timestamps(turns)

    def _smooth_turn_timestamps(
        self,
        turns: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Smooth and fill missing boundary timestamps across adjacent turns."""
        for i, turn in enumerate(turns):
            if turn["start"] is None and i > 0 and turns[i - 1]["end"] is not None:
                turn["start"] = turns[i - 1]["end"]
            if turn["start"] is None:
                turn["start"] = 0.0
            if turn["end"] is None and i < len(turns) - 1 and turns[i + 1]["start"] is not None:
                turn["end"] = turns[i + 1]["start"]
            if turn["end"] is None or turn["end"] < turn["start"]:
                turn["end"] = turn["start"]
        return turns

    def reproject_semantic_paragraphs(
        self,
        turn_words: List[Dict[str, Any]],
        paragraphs: List[str],
        fallback_start: float = 0.0,
        fallback_end: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        Re-project LLM semantically segmented paragraphs onto physical word timestamps.
        Returns a list of dicts: [{"text": str, "start": float, "end": float}, ...].
        Guarantees zero LLM timestamp hallucination while enabling semantic paragraph splitting.
        """
        clean_paras = [p.strip() for p in paragraphs if p and p.strip()]
        if not clean_paras:
            return []
        if len(clean_paras) == 1:
            st = turn_words[0]["start"] if (turn_words and turn_words[0].get("start") is not None) else fallback_start
            et = turn_words[-1]["end"] if (turn_words and turn_words[-1].get("end") is not None) else fallback_end
            return [{"text": clean_paras[0], "start": st, "end": max(st, et)}]

        # When underlying word-level timestamps are available, project via character/token LCS
        if turn_words:
            word_units = self._build_alignment_units(turn_words, is_micro=True)
            if word_units:
                para_units: List[Dict[str, Any]] = []
                for p_idx, p_text in enumerate(clean_paras):
                    # Split paragraph into words/characters using the same unit builder
                    pseudo_words = [{"word": tok} for tok in re.findall(r"\S+", p_text)]
                    p_u = self._build_alignment_units(pseudo_words, is_micro=False)
                    for u in p_u:
                        u["para_idx"] = p_idx
                        para_units.append(u)

                norm_para = [u["token"] for u in para_units]
                norm_word = [u["token"] for u in word_units]
                matcher = SequenceMatcher(None, norm_para, norm_word, autojunk=False)

                para_bounds: List[Dict[str, Optional[float]]] = [
                    {"start": None, "end": None} for _ in clean_paras
                ]
                for block in matcher.get_matching_blocks():
                    for i in range(block.size):
                        pu = para_units[block.a + i]
                        wu = word_units[block.b + i]
                        p_idx = pu["para_idx"]
                        st = wu["start"]
                        et = wu["end"]
                        if st is not None:
                            if para_bounds[p_idx]["start"] is None or st < para_bounds[p_idx]["start"]:
                                para_bounds[p_idx]["start"] = st
                        if et is not None:
                            if para_bounds[p_idx]["end"] is None or et > para_bounds[p_idx]["end"]:
                                para_bounds[p_idx]["end"] = et

                # Ensure complete monotonic bounds across all paragraphs
                total_st = word_units[0]["start"] if word_units[0]["start"] is not None else fallback_start
                total_et = word_units[-1]["end"] if word_units[-1]["end"] is not None else fallback_end
                if para_bounds[0]["start"] is None:
                    para_bounds[0]["start"] = total_st
                if para_bounds[-1]["end"] is None:
                    para_bounds[-1]["end"] = total_et

                for idx in range(len(clean_paras)):
                    if para_bounds[idx]["start"] is None:
                        prev_et = para_bounds[idx - 1]["end"] if idx > 0 else total_st
                        para_bounds[idx]["start"] = prev_et if prev_et is not None else total_st
                    if para_bounds[idx]["end"] is None:
                        next_st = (
                            para_bounds[idx + 1]["start"]
                            if (idx + 1 < len(clean_paras) and para_bounds[idx + 1]["start"] is not None)
                            else total_et
                        )
                        para_bounds[idx]["end"] = max(para_bounds[idx]["start"], next_st)
                    # Snap contiguous boundary so paragraph i+1 starts smoothly at paragraph i end
                    if idx > 0 and para_bounds[idx - 1]["end"] is not None:
                        if para_bounds[idx]["start"] < para_bounds[idx - 1]["end"]:
                            para_bounds[idx]["start"] = para_bounds[idx - 1]["end"]
                        if para_bounds[idx]["end"] < para_bounds[idx]["start"]:
                            para_bounds[idx]["end"] = para_bounds[idx]["start"]

                return [
                    {
                        "text": clean_paras[idx],
                        "start": float(para_bounds[idx]["start"]),
                        "end": float(para_bounds[idx]["end"]),
                    }
                    for idx in range(len(clean_paras))
                ]

        # Proportional character-length fallback when word-level array is not present
        char_counts = [max(1, len(normalize_multilingual_token(p))) for p in clean_paras]
        total_chars = sum(char_counts)
        total_dur = max(0.0, fallback_end - fallback_start)
        results: List[Dict[str, Any]] = []
        cur_t = fallback_start
        for idx, p_text in enumerate(clean_paras):
            ratio = char_counts[idx] / total_chars
            p_dur = total_dur * ratio
            next_t = fallback_end if idx == len(clean_paras) - 1 else (cur_t + p_dur)
            results.append({
                "text": p_text,
                "start": round(cur_t, 3),
                "end": round(max(cur_t, next_t), 3),
            })
            cur_t = next_t
        return results
