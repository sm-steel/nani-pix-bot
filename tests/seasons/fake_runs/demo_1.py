from types import MappingProxyType

from nani_pix_bot.seasons.definition import Gate, GateTag, SeasonRun, XpTable

RUN = SeasonRun(
    run_id="demo_1",
    names=MappingProxyType({"EN": "Demo Season", "RU": "Демо-сезон"}),
    xp=XpTable(win_by_stage=(50, 40, 30, 20, 10), host_solved=15, host_unsolved=5, first_guess=5),
    gate=Gate(
        any_of=(GateTag("genre", "Romance", 22, shikimori_id=22),),
        description=MappingProxyType({"EN": "Romance", "RU": "Романтика"}),
    ),
)
