from django.db import migrations

KENYA_DATA = {
    "Nairobi": {
        "Westlands": ["Kitisuru", "Parklands/Highridge", "Karura", "Kangemi", "Mountain View"],
        "Dagoretti North": ["Kilimani", "Kawangware", "Gatina", "Kileleshwa", "Kabiro"],
        "Dagoretti South": ["Mutu-ini", "Ngando", "Riruta", "Uthiru/Ruthimitu", "Waithaka"],
        "Langata": ["Karen", "Nairobi West", "Mugumu-ini", "South C", "Nyayo Highrise"],
        "Kibra": ["Laini Saba", "Lindi", "Makina", "Woodley/Kenyatta Golf Course", "Sarang'ombe"],
        "Roysambu": ["Githurai", "Kahawa West", "Zimmerman", "Roysambu", "Kahawa"],
        "Kasarani": ["Clay City", "Mwiki", "Kasarani", "Njiru", "Ruai"],
        "Ruaraka": ["Baba Dogo", "Utalii", "Mathare North", "Lucky Summer", "Korogocho"],
        "Embakasi South": ["Imara Daima", "Kwa Njenga", "Kwa Reuben", "Pipeline", "Kware"],
        "Embakasi North": ["Kariobangi North", "Dandora Area I", "Dandora Area II", "Dandora Area III", "Dandora Area IV"],
        "Embakasi Central": ["Kayole North", "Kayole Central", "Kayole South", "Komarock", "Matopeni/Spring Valley"],
        "Embakasi East": ["Upper Savanna", "Lower Savanna", "Embakasi", "Utawala", "Mihango"],
        "Embakasi West": ["Umoja I", "Umoja II", "Mowlem", "Kolumutu"],
        "Makadara": ["Maringo/Hamza", "Viwandani", "Harambee", "Makongeni"],
        "Kamukunji": ["Pumwani", "Eastleigh North", "Eastleigh South", "Airbase", "California"],
        "Starehe": ["Stadium", "Ngara", "Pangani", "Ziwani/Kariokor", "Landimawe", "Nairobi Central"],
        "Mathare": ["Hospital", "Mabatini", "Huruma", "Ngei", "Mlango Kubwa", "Kiamaiko"],
    },
    "Mombasa": {
        "Changamwe": ["Port Reitz", "Kipevu", "Airport", "Changamwe", "Chaani"],
        "Jomvu": ["Jomvu Kuu", "Mikindani", "Miritini"],
        "Kisauni": ["Mjambere", "Junda", "Bamburi", "Mwakirunge", "Mtopanga", "Magogoni", "Shanzu"],
        "Nyali": ["Frere Town", "Ziwa La Ng'ombe", "Mkomani", "Kongowea", "Kadzandani"],
        "Likoni": ["Mtongwe", "Shika Adabu", "Bofu", "Likoni", "Timbwani"],
        "Mvita": ["Mji Wa Kale/Makadara", "Tudor", "Tononoka", "Shimanzi/Ganjoni", "Majengo"],
    },
    "Kwale": {
        "Msambweni": ["Gombato Bongwe", "Ukunda", "Kinondo", "Ramisi"],
        "Lungalunga": ["Pongwe/Kikoneni", "Dzombo", "Mwereni", "Vanga"],
        "Matuga": ["Tsimba Golini", "Waa", "Tiwi", "Kubo South", "Mackinnon Road"],
        "Kinango": ["Ndavaya", "Puma", "Kinango", "Mackinnon Road", "Chengoni/Samburu", "Amani/Maluganji"],
    },
    "Kilifi": {
        "Kilifi North": ["Tezo", "Sokoni", "Kibarani", "Dabaso", "Matsangoni", "Watamu", "Mnarani"],
        "Kilifi South": ["Junju", "Mwarakaya", "Shimo La Tewa", "Chasimba", "Mtepeni"],
        "Kaloleni": ["Mariakani", "Kayafungo", "Kaloleni", "Mwanamwinga"],
        "Rabai": ["Rabai/Kisurutini", "Ruruma", "Kambe/Ribe"],
        "Ganze": ["Ganze", "Bamba", "Jaribuni", "Sokoke"],
        "Malindi": ["Jilore", "Kakuyuni", "Ganda", "Malindi Town", "Shella"],
        "Magarini": ["Marafa", "Magarini", "Gongoni", "Adu", "Garashi", "Sabaki"],
    },
    "Tana River": {
        "Garsen": ["Garsen South", "Garsen Central", "Garsen North", "Garsen West", "Kipini West", "Garsen East"],
        "Galole": ["Kinakomba", "Mikinduni", "Chewani", "Wayu"],
        "Bura": ["Chewele", "Bura", "Bangale", "Sala", "Madogo"],
    },
    "Lamu": {
        "Lamu East": ["Faza", "Kiunga", "Basuba"],
        "Lamu West": ["Shela", "Mkomani", "Hindi", "Mavneutral", "Witu", "Hongwe", "Mkunumbi", "Hidabu"],
    },
    "Taita Taveta": {
        "Taveta": ["Chala", "Mahoo", "Bomani", "Mboghoni", "Mata"],
        "Wundanyi": ["Wundanyi/Mbale", "Werugha", "Wumingu/Kishushe", "Mwanda/Mgange"],
        "Mwatate": ["Ronge", "Mwatate", "Bura", "Chawia", "Wusi/Kishamba"],
        "Voi": ["Mbololo", "Sagala", "Kaloleni", "Marungu", "Ngolia"],
    },
    "Garissa": {
        "Garissa Township": ["Iftin", "Waberi", "Galbet", "Township", "Waco"],
        "Balambala": ["Balambala", "Danyere", "Jarajila", "Gondoro", "Kyarero"],
        "Lagdera": ["Modogashe", "Benane", "Goreale", "Maalimin", "Sabena", "Baraki"],
        "Dadaab": ["Daadab", "Labasigale", "Damajale", "Liboi", "Abakaile"],
        "Fafi": ["Bura", "Dekaharia", "Jarajila", "Fafi", "Nanighi"],
        "Ijara": ["Ijara", "Masalani", "Sangailu", "Hulugho"],
    },
    "Wajir": {
        "Wajir North": ["Gurar", "Bute", "Korondile", "Malkagufu", "Batalu", "Danaba"],
        "Wajir East": ["Wajir Township", "Khorof/Harar", "Hadado/Athibohol"],
        "Tarbaj": ["Tarbaj", "Wargadud", "Sarman", "Muddobay"],
        "Wajir West": ["Ganyure/Wagberi", "Sheraru", "Elben", "Hadado"],
        "Eldas": ["Eldas", "Qaramdhure", "Mustahil", "Lagboghol South"],
        "Wajir South": ["Ademasajida", "Habaswein", "Lagboghol North", "Diif"],
    },
    "Mandera": {
        "Mandera East": ["Neboi", "Warankara", "Libehia", "Rhamu", "Rhamu Dimtu"],
        "Banissa": ["Banissa", "Derkhale", "Gari", "Malkamari", "Dubai/Olla"],
        "Mandera North": ["Ashabito", "Morothile", "Lagsure", "Dandu", "Guticha"],
        "Mandera South": ["Wanlamadhow", "Khalalio", "Nezerini", "Alasan", "Kiliwehiri"],
        "Mandera West": ["Takaba", "Takaba South", "Kamuthe", "Gither"],
        "Lafey": ["Lafey", "Wasinke", "Lafey Township", "Sala"],
    },
    "Marsabit": {
        "Moyale": ["Moyale Township", "Butiye", "Sololo", "Heillu/Manyatta"],
        "North Horr": ["Dukana", "Maikona", "Turbi", "North Horr", "Illeret"],
        "Saku": ["Marsabit Central", "Lontolio", "Sagante/Jaldesa", "Karare"],
        "Laisamis": ["Laisamis", "Logologo", "Loiyangalani", "Mt. Kulal"],
    },
    "Isiolo": {
        "Isiolo North": ["Wabera", "Chari", "Bulla Pesa", "Oldo/Uran", "Ngare Mara", "Burat", "Central"],
        "Isiolo South": ["Garbatulla", "Kinna", "Sericho"],
    },
    "Meru": {
        "Igembe South": ["Maua", "Kiegoi/Antubetwe Kiongo", "Athiru Gaiti", "Akachiu", "Kanuni"],
        "Igembe Central": ["Igembe", "Njia", "Township", "Bambe", "Consolata", "Antuambui"],
        "Igembe North": ["Ntunene", "Antubetwe Kiongo", "Naathu", "Amwathi"],
        "Tigania West": ["Athiru Ruujine", "Athi", "Mbeu", "Mikinduri", "Kianjai"],
        "Tigania East": ["Nkomo", "Mukothima", "Njia", "Township", "Karama"],
        "Central Imenti": ["Municipality", "Ntima East", "Ntima West", "Nkuene"],
        "North Imenti": ["Mitunguu", "Igoji East", "Igoji West", "Abogeta East", "Abogeta West", "Nkuene"],
        "South Imenti": ["Mwimbi", "Muthambi", "Kiguchwa", "Miathene"],
        "Buuri": ["Timau", "Kisima", "Kiirua/Naari", "Ruiri/Rwarera"],
    },
    "Tharaka Nithi": {
        "Maara": ["Nkuene", "Muthambi", "Kiguchwa", "Mwimbi", "Ganga", "Chogoria"],
        "Chuka/Igambang'ombe": ["Mariani", "Karingani", "Magumoni", "Mugwe", "Igambang'ombe"],
        "Tharaka": ["Tharaka North", "Tharaka South", "Marimanti", "Gatunga", "Mukothima"],
    },
    "Embu": {
        "Manyatta": ["Ruguru/Ngandori", "Kithimu", "Nginda", "Mbeti North", "Kianjiru"],
        "Runyenjes": ["Central Runyenjes", "Kabare", "Kagaari South", "Kagaari North", "Kyeni North", "Kyeni South"],
        "Mbeere South": ["Mbeere South", "Mavuria", "Kiambere", "Mbeti South"],
        "Mbeere North": ["Evurore", "Mwea", "Makima", "Nthawa", "Mariari"],
    },
    "Kitui": {
        "Mwingi North": ["Kyuso", "Mumoni", "Tseikuru", "Mwingi Central"],
        "Mwingi West": ["Ngomeni", "Kyome/Thaana", "Nguutani", "Migwani", "Kiomo/Kyethani"],
        "Mwingi Central": ["Kivou", "Nguni", "Nuu", "Mui", "Waita"],
        "Kitui West": ["Mutonguni", "Kauwi", "Matinyani", "Kwa Mutonga/Kithumula"],
        "Kitui Rural": ["Kisasi", "Mwitika", "Mutitu/Kaliku", "Pangani", "Kwavonza/Yatta"],
        "Kitui Central": ["Miambani", "Township", "Kyangwithya West", "Mulundi", "Kyangwithya East"],
        "Kitui East": ["Zombe/Mwitika", "Chuluni", "Nzambani", "Voo/Kasue", "Endau/Malalani"],
        "Kitui South": ["Ikutha", "Kanziku", "Nzau/Kiliku", "Mutomo", "Kiio"],
    },
    "Machakos": {
        "Masinga": ["Masinga Central", "Ekalakala", "Muthesya", "Ndithini", "Kivaa"],
        "Yatta": ["Matuu", "Kithimani", "Ikombe", "Katangi", "Kalama", "Ndalani", "Barikia"],
        "Kangundo": ["Kangundo North", "Kangundo Central", "Kangundo East", "Kangundo West"],
        "Matungulu": ["Tala", "Matungulu North", "Matungulu East", "Matungulu West", "Kyeleni"],
        "Kathiani": ["Mitaboni", "Kathiani Central", "Upper Kaewa/Iveti", "Lower Kaewa"],
        "Mavoko": ["Athi River", "Kinanie", "Muthwani", "Syokimau/Mulolongo"],
        "Machakos Town": ["Machakos Central", "Mutituni", "Muvuti/Kiima Kimwe", "Mumbuni North", "Kalama", "Mua"],
        "Mwala": ["Mbiuni", "Makaveti/Kiteta", "Kibauni", "Nguumo", "Kasikeu"],
    },
    "Makueni": {
        "Mbooni": ["Tulimani", "Mbooni", "Kithungo/Kitundu", "Kiima Kimwe/Kalawa", "Mbooni East"],
        "Kilome": ["Kasikeu", "Mukaa", "Kiima Kimwe"],
        "Kaiti": ["Kaiti", "Mukaa", "Nzaui/Kilili/Kalamba", "Mbooni"],
        "Makueni": ["Wote", "Muvau/Kikumini", "Mavindini", "Kitise/Kithuki", "Kathonzweni", "Nzaui/Kalamba"],
        "Kibwezi West": ["Makindu", "Nguumo", "Kikumbulyu North", "Kikumbulyu South", "Nguu/Masumba", "Emali/Mulala"],
        "Kibwezi East": ["Kibwezi", "Masongaleni", "Mtito Andei", "Thange", "Ivingoni/Nzambani"],
    },
    "Nyandarua": {
        "Kinangop": ["Gathara", "North Kinangop", "Murungaru", "Njabini/Kiburu", "Nyakio"],
        "Kipipiri": ["Geta", "Githioro", "Kipipiri", "Wanjohi"],
        "Ol Kalou": ["Karau", "Kanjuiri Ridge", "Mirangine", "Kaimbaga", "Rurii"],
        "Ol Joro Orok": ["Ng'arua", "Karandi", "Ol Joro Orok", "Shamata"],
        "Ndaragwa": ["Leshau", "Pondo", "Ndaragwa", "Central"],
    },
    "Nyeri": {
        "Tetu": ["Dedan Kimathi", "Wamagana", "Aguthi/Gaaki"],
        "Kieni": ["Mwiyogo/Endarasha", "Nyakio", "Kabaru", "Gatarakwa", "Ragati"],
        "Mathira": ["Ruguru", "Karatina Town", "Mahiga", "Iria-ini", "Chinga", "Konyu"],
        "Othaya": ["Muhoya", "Karange", "Othaya/Korogocho", "Gititu/Kanganye"],
        "Mukurweini": ["Gikondi", "Rugi", "Mukurweini/Rugi", "Aguthi"],
        "Nyeri Town": ["Rware", "Gatitu/Muruguru", "Ruring'u", "Kiganjo/Mathari"],
    },
    "Kirinyaga": {
        "Mwea": ["Mutithi", "Kangai", "Wamumu", "Nyangati", "Murinduko", "Gathigiriri", "Tebere"],
        "Gichugu": ["Kabare", "Baragwi", "Njukiini", "Ngariama", "Kerugoya"],
        "Ndia": ["Gatitu/Muruguru", "Mukure/Ndia", "Kiine", "Karumandi"],
        "Kirinyaga Central": ["Mutira", "Kanyekini", "Kerugoya", "Inoi"],
    },
    "Murang'a": {
        "Kiharu": ["Wangu", "Mugoiri", "Mbiri", "Township", "Murarandia", "Gaturi"],
        "Kigumo": ["Kigumo", "Kinyona", "Muthithi", "Ithiru", "Ruchu"],
        "Maragwa": ["Kimorori/Wempa", "Makuyu", "Kambiti", "Kamahuha", "Ichagaki", "Nginda"],
        "Kandara": ["Ng'araria", "Muruka", "Kagundu-ini", "Gaichanjiru", "Ithiga", "Ikumbi", "Ndakaini"],
        "Gatanga": ["Ithanga", "Kakuzi/Mitubiri", "Mugumo-ini", "Township", "Kihumbu-ini", "Gatanga", "Kariara"],
        "Kahuro": ["Kanyenya-ini", "Wiumiririe", "Mairi", "Kahuro"],
        "Mathioya": ["Gitugi", "Kamacharia", "Kiru"],
    },
    "Kiambu": {
        "Gatundu South": ["Kiganjo/Mathua", "Ndarugu", "Ngenda", "Kiaora", "Githobokoni"],
        "Gatundu North": ["Gituamba", "Kariara", "Gitaro/Mang'u", "Kiuu", "Nyakio"],
        "Juja": ["Murera", "Theta", "Juja", "Witeithie", "Kalimoni"],
        "Thika Town": ["Kamenu", "Hospital", "Gatuanyaga", "Ngoliba", "Chania"],
        "Ruiru": ["Gitothua", "Biashara", "Gatongora", "Kahawa Sukari", "Kahawa Wendani", "Mwiki", "Mwihoko"],
        "Githunguri": ["Githunguri", "Githiga", "Ikinu", "Ngewa", "Komothai"],
        "Kiambu": ["Kiambu", "Ting'ang'a", "Ndenderu", "Kabete", "Cianda"],
        "Kiambaa": ["Cianda", "Karuri", "Kihara", "Ndenderu", "Muchatha", "Kambiti"],
        "Kabete": ["Gitaru", "Muguga", "Nyadhuna", "Kabete", "Uthiru"],
        "Kikuyu": ["Karai", "Nachu", "Sigona", "Kikuyu", "Kinoo"],
        "Limuru": ["Bibirioni", "Limuru Central", "Ndeiya", "Limuru East", "Ngecha/Tigoni"],
        "Lari": ["Kijabe", "Nyanduma", "Kinale", "Kamburu", "Lari/Kirenga"],
    },
    "Turkana": {
        "Turkana North": ["Letea", "Lokwamosing", "Lapur", "Kaaleng/Kaikor", "Kibish"],
        "Turkana West": ["Kakuma", "Lopur", "Lokichoggio", "Songot", "Kalobeyei", "Nanaam"],
        "Turkana Central": ["Kalokol", "Lodwar Township", "Kanamkemer", "Lobei", "Naumit", "Kangatotha", "Kang'atotha"],
        "Loima": ["Loima", "Turkwel", "Lorugum", "Pelekech"],
        "Turkana South": ["Kerio Delta", "Lokichar", "Kainuk", "Kalemngorok/Mogila", "Katilu", "Lobokat", "Kaputir"],
        "Turkana East": ["Kapedo/Napeitom", "Katilu", "Lomelo", "Lokori/Kochodin"],
    },
    "West Pokot": {
        "Kapenguria": ["Sook", "Riwo", "Kapenguria", "Mnagei", "Siyoi", "Endugh", "Kodich"],
        "Sigor": ["Sekerr", "Masool", "Lomut", "Weiwei"],
        "Kacheliba": ["Kacheliba", "Kapchok", "Kassait", "Kakimat", "Alale"],
        "Pokot South": ["Wekor", "Chepareria", "Batei", "Lelan", "Tapach"],
    },
    "Samburu": {
        "Samburu West": ["Suguta Marmar", "Maralal", "Loosuk", "Poro", "El Barta"],
        "Samburu North": ["Waso", "Archers Post", "Kurungu", "Lodokejek", "Supuko"],
        "Samburu East": ["Wamba West", "Wamba East", "Wamba North"],
    },
    "Trans Nzoia": {
        "Kwanza": ["Kwanza", "Keiyo", "Bidii", "Kabuyefwe"],
        "Endebess": ["Endebess", "Chepchoina", "Matumbei"],
        "Saboti": ["Matisi", "Tuwani", "Saboti", "Machewa"],
        "Kiminini": ["Kiminini", "Waitaluk", "St. Joseph", "Sikhendu", "Nabiswa"],
        "Cherangany": ["Sinyerere", "Makutano", "Kaplamai", "Motosiet", "Cherangany/Suwerwa", "Chepsiro/Kiptoror", "Sirikwa"],
    },
    "Uasin Gishu": {
        "Soy": ["Ziwa", "Ledeo", "Soy", "Moi's Bridge", "Kipsomba"],
        "Turbo": ["Turbo", "Ngenyilel", "Tapsagoi", "Kamagut", "Huruma"],
        "Moiben": ["Moiben", "Tembelio", "Sergoit", "Karuna/Meibeki", "Mochorwa"],
        "Ainabkoi": ["Ainabkoi/Olare", "Kapsoya", "Kaptagat"],
        "Kapseret": ["Simat/Kapseret", "Kipkenyo", "Ngeria", "Megun", "Langas"],
        "Kesses": ["Tarakwa", "Megun", "Sugoi", "Kesses", "Racecourse"],
    },
    "Elgeyo Marakwet": {
        "Marakwet East": ["Lelan", "Soy", "Moiben", "Sambirir", "Arror", "Embobut/Embulot"],
        "Marakwet West": ["Cherangany", "Kapyego", "Markwet", "Lelan"],
        "Keiyo North": ["Emsoo", "Kamariny", "Kaptiony", "Tambach"],
        "Keiyo South": ["Mosop", "Metkei", "Roroek", "Chepkorio", "Soy North"],
    },
    "Nandi": {
        "Tinderet": ["Tinderet", "Songhor/Soba", "Chemelil/Chemase", "Kapsimotwa"],
        "Aldai": ["Kabwareng", "Terik", "Kemeloi-Maraba", "Kobujoi", "Kaptumo-Kaboi", "Nandi Hills"],
        "Nandi Hills": ["Nandi Hills", "Chepkunyuk", "Ol'lessos", "Kapchorua"],
        "Chesumei": ["Kosirai", "Lelmokwo/Ngechek", "Chemundu/Kapng'etuny", "Chepterwai"],
        "Emgwen": ["Kilibwoni", "Chepkumia", "Kaptel/Kamoiywo", "Kipkaren"],
        "Mosop": ["Kabisaga", "Kipkaren East", "Kipkaren West", "Kapsabet", "Ndalat"],
    },
    "Baringo": {
        "Tiaty": ["Tirioko", "Kolowa", "Ribkwo", "Silale", "Loiyamorock", "Tangulbei/Korossi"],
        "Baringo North": ["Baringo Central", "Bartabwa", "Saimo/Soi", "Saimo/Kipsaraman", "Kabartonjo"],
        "Baringo Central": ["Kabimoi", "Tenges", "Ewalel/Chapchap", "Mochongoi", "Mukutani"],
        "Baringo South": ["Marigat", "Ilchamus", "Mochongoi", "Mukutani"],
        "Eldama Ravine": ["Ravine", "Koibatek", "Maji Mazuri", "Lembus", "Lembus Kwen", "Lembus Perkerra"],
        "Mogotio": ["Mogotio", "Emining", "Kisanana"],
    },
    "Laikipia": {
        "Laikipia West": ["Ol-Moran", "Rumuruti Township", "Githiga", "Igwamiti", "Salama"],
        "Laikipia East": ["Ngobit", "Tigithi", "Thingithu", "Nanyuki"],
        "Laikipia North": ["Mukogondo West", "Mukogondo East"],
    },
    "Nakuru": {
        "Molo": ["Molo", "Turi", "Mariashoni", "Elburgon", "Njoro"],
        "Njoro": ["Njoro", "Mauche", "Kihingo", "Lare", "Kures"],
        "Naivasha": ["Naivasha East", "Viwandani", "Hells Gate", "Lake View", "Maai Mahiu", "Olkaria", "Naivasha West", "Biashara"],
        "Gilgil": ["Gilgil", "Elementaita", "Mbaruk/Eburu", "Malewa West", "Murindati"],
        "Nakuru Town West": ["Barut", "London", "Nakuru East", "Biashara", "Kivumbini"],
        "Nakuru Town East": ["Flamingo", "Menengai", "Nakuru East", "Biashara", "Kivumbini"],
        "Rongai": ["Menengai West", "Sorget", "Visoi", "Solai", "Mosop"],
        "Subukia": ["Subukia", "Waseges", "Kabazi"],
        "Bahati": ["Bahati", "Dundori", "Kabazi", "Lanet/Umoja"],
        "Kuresoi North": ["Kuresoi", "Sirikwa", "Kamara", "Olenguruone"],
        "Kuresoi South": ["Kedowa/Kimugul", "Amalo", "Kipkelion", "Roret"],
    },
    "Narok": {
        "Kilgoris": ["Kilgoris Central", "Keyian", "Angata Barikoi", "Shankoe", "Kimintet", "Lolgorian"],
        "Emurua Dikirr": ["Emurua Dikirr", "Kileghen", "Nkai/Naroosura", "Ololulung'a"],
        "Narok North": ["Olpusimoru", "Olokurto", "Narok Town", "Nkareta", "Olorropil", "Melelo", "Loita"],
        "Narok East": ["Mosiro", "Ildamat", "Keekonyokie", "Dalalekutuk", "Mailua"],
        "Narok South": ["Majimoto/Naroosura", "Olalaiser", "Mara", "Siana", "Naikarra"],
        "Narok West": ["Ilkisonko", "Kelegon", "Old Ranch", "Melelo", "Loita", "Sogoo", "Sagamian"],
    },
    "Kajiado": {
        "Kajiado North": ["Ngong", "Oloolua", "Nkaimurunya", "Olosirkon/Sholinke", "Keekonyokie"],
        "Kajiado Central": ["Purko", "Ildamat", "Dalalekutuk", "Mosiro", "Kajiado Central"],
        "Kajiado East": ["Imaroro", "Kaputiei North", "Kitengela", "Oloosirkon/Sholinke", "Kenyawa/Poka", "Iloodokilani"],
        "Kajiado West": ["Keekonyokie", "Ilkisonko", "Entonet/Lenkisem", "Magadi", "Ewuaso Oonkidong'i"],
        "Kajiado South": ["Mosiro", "Illasit", "Kuku", "Imaroro", "Entonet"],
    },
    "Kericho": {
        "Kipkelion East": ["Londiani", "Kedowa/Kimugul", "Kipkelion", "Chilchila"],
        "Kipkelion West": ["Kapsoit", "Kabianga", "Chemosot", "Litein", "Cheplanget", "Kapkugerwet"],
        "Ainamoi": ["Ainamoi", "Kapkures", "Kipsigak", "Kabornet"],
        "Bureti": ["Tebesonik", "Cheptororiet/Seretut", "Ndanai/Abosi", "Koiwa", "Chemaner"],
        "Belgut": ["Kabianga", "Kembu", "Waldai", "Cheptororiet", "Kisiara"],
        "Sigowet/Soin": ["Soin", "Sigowet", "Kaiplangat", "Barsiele"],
    },
    "Bomet": {
        "Sotik": ["Ndanai/Abosi", "Chemagel", "Kembu", "Longisa", "Sigor"],
        "Chepalungu": ["Sigor", "Merigi", "Kembu", "Kongasis", "Nyangores"],
        "Bomet East": ["Merigi", "Kembu", "Longisa", "Sigor", "Ndanai"],
        "Bomet Central": ["Silibwet Township", "Ndaraweta", "Singorwet", "Chesoen", "Mutarakwa"],
        "Konoin": ["Kimulot", "Mogogosiek", "Chepchabas", "Embomos", "Sirien"],
    },
    "Kakamega": {
        "Lugari": ["Lumakanda", "Lugari", "Mautuma", "Lwandeti"],
        "Likuyani": ["Sango", "Nzoia", "Likuyani", "Sinoko", "Kwanza"],
        "Malava": ["Chemuche", "Ileho", "Isukha Central", "Butali/Chegulo", "Manda/Shivanga", "Shirugu/Mugai"],
        "Lurambi": ["East Kabras", "Butsotso East", "Butsotso South", "Butsotso Central", "Sheywe", "Mahiakalo", "Ilesi"],
        "Navakholo": ["Ingotse/Matungu", "Nambachi", "Navakholo", "West Kabras"],
        "Mumias West": ["Mumias Central", "Mumias North", "Etenje", "Musanda"],
        "Mumias East": ["East Wanga", "Makunga", "Matungu", "Koyonzo"],
        "Matungu": ["Koyonzo", "Kholera", "Lusheya/Lubinu", "Butali"],
        "Butere": ["South Wanga", "Marama West", "Marama Central", "Marama North", "Marama East"],
        "Khwisero": ["East Wanga", "Kidundu", "Marama West", "Khwisero"],
        "Shinyalu": ["Isukha North", "Isukha South", "Isukha West", "Murhanda", "Isukha Central"],
        "Ikolomani": ["Idakho South", "Idakho East", "Idakho North", "Idakho Central"],
    },
    "Vihiga": {
        "Vihiga": ["Lugaga/Wamuluma", "Central Maragoli", "South Maragoli", "Sabatia"],
        "Sabatia": ["Wodanga", "Sabatia", "Chavakali", "North Maragoli", "Givogi", "Lyaduywa/Izava"],
        "Hamisi": ["Shiru", "Gisambai", "Tambua", "Jepkoyai", "Hamisi"],
        "Luanda": ["Luanda Township", "Wemilabi", "Mwibona", "Luanda South", "Emabungo"],
        "Emuhaya": ["North East Bunyore", "Central Bunyore", "West Bunyore"],
    },
    "Bungoma": {
        "Mt. Elgon": ["Cheptais", "Chesikaki", "Chepyuk", "Kopsiro", "Kaptama"],
        "Sirisia": ["Namwela", "Malakisi/South Kulisiru", "Lwandanyi"],
        "Kabuchai": ["Kabuchai/Chwele", "West Nalondo", "Bwake/Luuya", "Mukuyuni"],
        "Bumula": ["South Bukusu", "Bumula", "Khasoko", "Kabula", "Kimaeti", "West Bukusu/Sitikho", "Magacha", "Namamali"],
        "Kanduyi": ["Bukembe West", "Bukembe East", "Township", "Khalaba", "Musikoma", "East Sangalo", "West Sangalo", "Central Sangalo"],
        "Webuye East": ["Mihuu", "Ndivisi", "Maraka"],
        "Webuye West": ["Sitikho", "Matulo", "Bokoli"],
        "Kimilili": ["Kimilili", "Maeni", "Kamukuywa", "Kibingei"],
        "Tongaren": ["Mbakalo", "Naitiri/Kabuyefwe", "Milima", "Ndalu/Tabani", "Tongaren", "Soysambu/Mitua"],
    },
    "Busia": {
        "Teso North": ["Malaba Central", "Malaba North", "Ang'urai South", "Ang'urai North", "Ang'urai East", "Malaba South"],
        "Teso South": ["Amukura West", "Amukura East", "Amukura Central", "Chakol South", "Chakol North"],
        "Nambale": ["Nambale Township", "Marachi Central", "Marachi East", "Marachi West", "Marachi North"],
        "Matayos": ["Elugulu", "Mayenje", "Matayos South", "Busibwabo", "Burumba"],
        "Butula": ["Budumba", "Namboboto Nambuku", "Nangina", "Ageng'a Nanguba", "Butula"],
        "Funyula": ["Funyula", "Gobei", "Mungatsi", "Ruambwa", "Sifuyo"],
        "Budalangi": ["Budalangi Central", "Budubusi", "Mundere", "Musoma", "Sibuka", "Sio Port"],
    },
    "Siaya": {
        "Ugenya": ["West Ugenya", "Ukwala", "North Ugenya", "East Ugenya"],
        "Ugunja": ["Ugunja", "Sigomre", "West Yimbo", "North Yimbo", "East Yimbo"],
        "Alego Usonga": ["Central Alego", "Siaya Township", "North Alego", "South East Alego", "Usonga", "West Alego"],
        "Gem": ["North Gem", "West Gem", "Central Gem", "East Gem", "South Gem", "Yala Township"],
        "Bondo": ["Bondo Township", "Usigu", "Central Sakwa", "North Sakwa", "South Sakwa", "West Sakwa"],
        "Rarieda": ["East Asembo", "West Asembo", "North Uyoma", "South Uyoma", "West Uyoma"],
    },
    "Kisumu": {
        "Kisumu East": ["Kolwa East", "Miwani", "Ombeyi", "Masogo/Nyang'oma", "Chemelil/Chemilil"],
        "Kisumu West": ["Southern Kolwa", "Central Kolwa", "Manyatta B", "Nyalenda A", "South West Kisumu"],
        "Kisumu Central": ["Railways", "Migosi", "Shaurimoyo/Kaloleni", "Market Milimani", "Kondele", "Nyalenda B"],
        "Seme": ["West Seme", "Central Seme", "East Seme", "North Seme"],
        "Nyando": ["Awasi/Onjiko", "Ahero", "Kabonyo/Kanyagwal", "Kobura", "East Kano/Wawidhi"],
        "Muhoroni": ["Miwani", "Ombeyi", "Masogo/Nyang'oma", "Chemelil", "Muhoroni/Koru"],
        "Nyakach": ["South Nyakach", "West Nyakach", "Central Nyakach", "Kasipul", "North Nyakach"],
    },
    "Homa Bay": {
        "Kasipul": ["West Kasipul", "Central Kasipul", "South Kasipul", "Kasipul Kabondo"],
        "Kabondo Kasipul": ["Kabondo East", "Kabondo West", "Kokwanyo/Kakelo", "Kojwach"],
        "Karachuonyo": ["North Karachuonyo", "Central Karachuonyo", "Kanyaluo", "Kibiri", "West Karachuonyo", "Kendu Bay Town"],
        "Rangwe": ["East Gem", "West Gem", "Kagan", "Kochia"],
        "Homa Bay Town": ["Homa Bay Central", "Homa Bay Arujo", "Homa Bay West", "Homa Bay East"],
        "Ndhiwa": ["Kwabwai", "Kanyadoto", "Kanyikela", "Kabuoch North", "Kabuoch South/Pala", "Kanyamwa Kosewe", "Kanyamwa Kotieno"],
        "Suba North": ["Gwassi North", "Gwassi South", "Kaksingri West", "Ruma Kaksingri"],
        "Suba South": ["Lambwe", "Gembe", "Mfangano Island", "Rusinga Island"],
    },
    "Migori": {
        "Rongo": ["North Kamagambo", "Central Kamagambo", "East Kamagambo", "South Kamagambo"],
        "Awendo": ["North Sakwa", "South Sakwa", "West Sakwa", "Central Sakwa"],
        "Suna East": ["Wiga", "Mwitenga", "Wasweta II", "Ragana-Oruba"],
        "Suna West": ["Wasimbete", "God Jope", "Suna Central", "Kakrao", "Kwa"],
        "Uriri": ["West Kanyamkago", "North Kanyamkago", "Central Kanyamkago", "East Kanyamkago", "South Kanyamkago"],
        "Nyatike": ["Kachieng", "Kanyasa", "North Kadem", "Macalder/Kanyarwanda", "Kaler", "Got Kachola", "Muhuru"],
        "Kuria West": ["Masaba", "Bukira East", "Bukira Central/Ikerege", "Isibania", "Makerero", "Tagare", "Ntimaru West"],
        "Kuria East": ["Gokeharaka/Getambwega", "Ntimaru East", "Nyabasi East", "Nyabasi West"],
    },
    "Kisii": {
        "Bonchari": ["Boochi/Tendere", "Bogetenga", "Bomariba", "Bogiakumu", "Riana"],
        "South Mugirango": ["Getenga", "Moticho", "Gegero", "Magenche", "BokiRevenue"],
        "Bomachoge Borabu": ["Kiamokama", "Bore", "Boochi/Borabu", "Magenche", "Gesima"],
        "Bobasi": ["Bobasi Chache", "Boitang'are", "Maji Mazuri", "Nyacheki", "Bobasi Bogetaorio", "Bobasi Masige West", "Bobasi Masige East", "Bobasi Central", "Bobasi Masige Central"],
        "Bomachoge Chache": ["Township", "Boochi/Tendere", "Gesima", "Magenche"],
        "Nyaribari Masaba": ["Nyaribari Masaba", "Getembe", "Borabu", "Sensi", "Iranda"],
        "Nyaribari Chache": ["Keumbu", "Kiogoro", "Birongo", "Ibeno", "Kisii Central", "Bonyamatuta", "Township"],
        "Kitutu Chache North": ["Monyerero", "Sensi", "Marani", "Kegogi"],
        "Kitutu Chache South": ["Bobaracho", "Kikoko", "Masimba", "Kegogi", "Nyatieko"],
    },
    "Nyamira": {
        "Kitutu Masaba": ["Rigoma", "Gachuba", "Gesima", "Maombi", "Nyamira Township", "Nyansiongo"],
        "West Mugirango": ["Bogichora", "Bosamaro", "Bonyamatuta", "Township"],
        "North Mugirango": ["Itibo", "Bomwagamo", "Bokeira", "Magwagwa", "Ekerenyo"],
        "Borabu": ["Metembe", "Borabu", "Moticho", "Getenga"],
    },
}


def seed_data(apps, schema_editor):
    County = apps.get_model('core', 'County')
    SubCounty = apps.get_model('core', 'SubCounty')
    Ward = apps.get_model('core', 'Ward')

    for county_name, subcounties in KENYA_DATA.items():
        try:
            county = County.objects.get(name=county_name)
            for subcounty_name, wards in subcounties.items():
                subcounty, _ = SubCounty.objects.get_or_create(
                    county=county,
                    name=subcounty_name
                )
                for ward_name in wards:
                    Ward.objects.get_or_create(
                        sub_county=subcounty,
                        name=ward_name
                    )
        except County.DoesNotExist:
            print(f"County not found: {county_name}")


def unseed_data(apps, schema_editor):
    Ward = apps.get_model('core', 'Ward')
    SubCounty = apps.get_model('core', 'SubCounty')
    Ward.objects.all().delete()
    SubCounty.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_subcounty_ward_delete_sublocation'),
    ]

    operations = [
        migrations.RunPython(seed_data, unseed_data),
    ]