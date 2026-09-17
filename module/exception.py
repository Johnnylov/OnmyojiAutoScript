class CampaignEnd(Exception):
    pass


class MapDetectionError(Exception):
    pass


class MapWalkError(Exception):
    pass


class MapEnemyMoved(Exception):
    pass


class CampaignNameError(Exception):
    pass


class ScriptError(Exception):
    # This is likely to be a mistake of developers, but sometimes a random issue
    pass


class ScriptEnd(Exception):
    pass


class GameStuckError(Exception):
    pass


class BattleTransitionTimeout(Exception):
    """A battle transition timed out; defer this task and continue scheduling."""


class ActivityPreparationTimeout(BattleTransitionTimeout):
    """Activity setup failed before climbing; recover and retry the unfinished task."""


class GameBugError(Exception):
    # An error has occurred in Azur Lane game client. Alas is unable to handle.
    # A restart should fix it.
    pass


class GameTooManyClickError(Exception):
    pass


class EmulatorNotRunningError(Exception):
    pass


class GameNotRunningError(Exception):
    pass


class GamePageUnknownError(Exception):
    pass


class RequestHumanTakeover(Exception):
    # Request human takeover
    # Alas is unable to handle such error, probably because of wrong settings.
    pass

class TaskEnd(Exception):
    pass


class TaskDeferred(Exception):
    """An unfinished task should be retried without reporting completion."""

    def __init__(self, message: str, retry_after: float = 120):
        super().__init__(message)
        self.retry_after = retry_after
