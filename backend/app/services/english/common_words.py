"""High-frequency English words that are not worth teaching.

`STOPWORDS` in :mod:`vocabulary` covers grammar words (articles, pronouns,
auxiliaries). This module covers the next tier: the everyday content words a
learner at A2/B1 already knows (``time``, ``people``, ``important``,
``schedule`` …). They are needed as a filter only — a "new word" list that
opens with *price ×2* is noise, not study material.

Kept offline and dependency-free on purpose: scanning must never need network
or a downloaded corpus.
"""

from __future__ import annotations

#: ~900 everyday words. Deliberately excludes anything above A2/B1.
COMMON_WORDS: frozenset[str] = frozenset(
    """
    able about above across act action add afternoon age ago agree air all allow
    almost alone along already also always amazing among amount animal another
    answer anymore anyone anything anyway apartment appear apple area arm around
    arrive art ask asleep attention aunt autumn available avoid awake away baby
    back bad bag ball bank bar base basket bath bathroom beach bean bear beat
    beautiful because become bed bedroom beer before begin beginning behind believe
    below best better between big bike bill bird bit black block blue board boat
    body boil book boot bored boring born borrow boss both bottle bottom bowl box
    boy bread break breakfast bring broke brother brown brush build building burn
    bus business busy but butter buy cake call camera camp can't candle car card
    care careful carry case cash cat catch cause center chair chance change cheap
    check cheese chicken child children choose church cinema circle city class
    clean clear clever climb clock close clothes cloud club coach coast coffee
    cold collect college color come comfortable common company compare complete
    computer continue control cook cool copy corner correct cost could count
    country couple course cousin cover cream create cross crowd cry culture cup
    cut dad daily dance danger dark date daughter day dead deal dear decide deep
    delicious describe desert design desk detail develop die different difficult
    dinner direction dirty discover discuss dish do doctor dog door double doubt
    down draw dream dress drink drive driver drop dry during each ear early earth
    east easy eat edge education egg eight either else email empty end enjoy
    enough enter entire environment equal evening event ever every everybody
    everyone everything everywhere exact example excellent except excited exciting
    exercise expect expensive experience explain eye face fact fail fall family
    famous far farm fast fat father favorite fear feel feeling festival few field
    fight fill film final find fine finger finish fire first fish fit five fix
    flat flight floor flower fly follow food foot football for forest forget fork
    form fortune forward four free fresh friend friendly from front fruit full
    fun funny future game garden gas gate general get gift girl give glad glass
    go goal god gold good goodbye government grade grand grandmother grass great
    green grey ground group grow guess guest guide guitar guy hair half hall hand
    happen happy hard hat hate have head health hear heart heat heavy hello help
    here high hill history hit hobby hold hole holiday home honest hope horse
    hospital hot hotel hour house how however huge human hundred hungry hurry
    hurt husband ice idea if ill imagine important improve include information
    inside instead interest interested interesting into introduce invite island
    job join joke journey juice jump just keep key kid kill kind king kitchen
    know knowledge lady lake land language large last late later laugh law lay
    lazy lead learn least leave left leg lesson let letter level library lie life
    light like line lion list listen little live local long look lose lost lot
    loud love low luck lucky lunch machine mad magazine main make man many map
    market marry match material math matter may maybe meal mean meaning meat
    medicine meet meeting member memory mention message method middle midnight
    might mile milk mind minute mirror miss mistake mix moment money month moon
    more morning most mother mountain mouse mouth move movie much museum music
    must name narrow nation natural nature near nearly neck need neighbor never
    new news next nice night nine nobody noise none noon normal north nose not
    note nothing notice now number nurse ocean of off office often oil old once
    one only open opinion opposite or orange order other outside over own page
    pain paint pair paper parent park part party pass past path patient pattern
    pay peace pen pencil people perfect perhaps period person phone photo pick
    picture piece pig place plan plant plate play player please pleasure plus
    point police polite poor popular position possible post potato power practice
    prefer prepare present president pretty price problem produce program promise
    protect proud provide public pull push put quarter queen question quick quiet
    quite race radio rain raise reach read ready real realize reason receive
    recent recognize record red reduce remember repeat reply report rest result
    return rich ride right ring rise river road rock roll roof room root round
    rule run sad safe salt same sand save say school science sea season seat
    second see seem sell send sense sentence serious serve service set seven
    several shape share sharp she shop short should shoulder shout show shower
    sick side sight sign similar simple since sing single sister sit six size
    skill skin sky sleep slow small smart smile snow so social soft some somebody
    someone something sometimes son song soon sorry sound soup south space speak
    special speed spend sport spring stand star start station stay still stone
    stop store story straight strange street strong student study stupid subject
    success successful such sudden sugar summer sun sure surprise sweet swim
    table take talk tall taste teach teacher team tell ten test than thank thanks
    that theater their them then there these thing think third thirsty this those
    though thought thousand three through throw ticket tidy tie time tired today
    together tomorrow tonight too tooth top total touch tour tourist toward town
    traffic train travel tree trip trouble true trust truth try turn twice two
    type uncle under understand unit university until up upon use useful usual
    usually vacation value vegetable very video view village visit voice wait
    wake walk wall want war warm wash waste watch water way wear weather website
    wedding week weekend weight welcome west wet what when where whether which
    while white who whole why wide wife wild will win wind window wine winter
    wish with woman wonder wonderful wood word work world worry worse worst worth
    would write wrong yard yeah year yellow yes yesterday yet young zero
    rice pizza sandwich chocolate tooth teeth vegetable vegetables cookie salad
    noodle soup snack fruit apple banana orange egg milk tea juice shower
    clock bedroom hallway elevator stairs app apps phone phones laptop
    """.split()
)

#: Common words that are *not* worth flagging even though they look advanced.
_ALSO_COMMON = """
    absolutely actually additional advice afford agree ahead allow although amazing
    amount announce anyway apparently apply appreciate approach argue argument
    arrange arrive article aside attend attention attitude attract audience
    average basic basically beautiful become behavior believe belong benefit
    boring borrow bother brain branch break breath brief brilliant budget busy
    calm cancel career careful category celebrate certain challenge chance change
    character charge chat cheap check choice choose circumstance clear clearly
    comfortable comment communicate community compete complain concern condition
    confident confirm confuse connect consider contact content continue convenient
    conversation convince cool correct cost course crazy create creative crowd
    curious current customer damage deal decide decision degree deliver demand
    depend describe description deserve design detail determine develop difference
    difficult direction directly disagree disaster discuss discussion doubt
    easily effect effort either employ encourage energy engage enjoy enormous
    ensure enter entire environment equipment escape essential establish event
    eventually evidence exact examine example excellent exchange excited exciting
    excuse exercise exist expect expensive experience explain express extra
    extremely familiar fancy fantastic fault favorite feature fee feeling figure
    fill finally fit focus follow force forget forgive fortunately free freedom
    fresh friendly frighten fully fun function funny further future gain gather
    general generally glad goal grab grateful great guess guide habit handle hang
    happen hardly hate healthy hear heat heavy helpful hesitate hire honest
    horrible however huge human hurry hurt ignore imagine immediately impact
    important impossible impress improve include increase indeed independent
    indicate individual influence information insist inspire instance instead
    interest interested interesting interview introduce invent invite involve
    issue item join joke journey judge keep kitchen knowledge lack language
    large last lately laugh lazy lead learn leave lend level lie lift likely
    limit listen lonely lose lovely low luck lucky main maintain major manage
    manner mark market marriage matter mean measure meet mention mess method
    middle might mind minor miss mistake mix moment mood move mud murder narrow
    natural nearly necessary nervous normal notice obviously occur offer often
    once opportunity option order ordinary organize otherwise outside own pain
    particular partner pass patient pattern pay peace perfect perform perhaps
    period permit person personal pick place plan pleasant please plenty point
    polite poor popular position positive possible pour power practical practice
    praise prefer prepare present pretty prevent price probably problem process
    produce progress promise proper protect proud prove provide public pull
    punish purpose push quality quarter question quiet quit quite raise range
    rather reach react ready realize really reason receive recent recognize
    recommend reduce refuse regard regular relax release remain remember remind
    remove repair repeat replace reply report represent request require rescue
    respect respond response rest result return review rich ride right ring rise
    risk rough round rude rule run rush safe satisfy save scare scene schedule
    scream search season seat seem sell send sense sensible serious serve service
    settle several shake share sharp shine shoot short shout show shut sick
    sight sign silence silly similar simple since single sit situation size skill
    skin sleep slight slow small smart smell smile social soft solve sort sound
    space speak special spend spot spread stand stare start state stay steal
    step stick still stomach stop store storm story straight strange street
    stress strike strong struggle student study stupid succeed success such
    sudden suffer suggest suit supply support suppose sure surprise surround
    survive sweet swim switch table take talent talk taste teach team tear
    technology tell terrible test thank thick thin think though thought threaten
    throw tidy tight tiny tip tired together tomorrow tonight total touch tough
    tour toward track trade traffic train travel treat tree trip trouble trust
    truth try turn twist type typical ugly uncle under understand unfortunately
    unhappy uniform unique unless until upset urgent useful usual usually value
    various vegetable vehicle victim video view village violence visit voice
    volume wait wake walk wall want warm warn wash waste watch water wave way
    weak wear weather week weight welcome well west wet whatever wheel while
    whisper whole wide wife wild willing win wind window wine winter wipe wise
    wish wonder wonderful wood word work worry worse worst worth wrap write
    wrong yard year yellow yes yesterday young zero
"""
COMMON_WORDS = COMMON_WORDS | frozenset(_ALSO_COMMON.split())

__all__ = ["COMMON_WORDS"]
