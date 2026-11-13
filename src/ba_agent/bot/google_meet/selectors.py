"""Meet selectors. Prefer accessible names. Each one has a fallback in the adapter."""

CAPTION_REGION = '[role="region"][aria-label*="aption" i], [data-ba-captions]'
TURN_ON_CAPTIONS = "Turn on captions"
TURN_OFF_CAPTIONS = "Turn off captions"
CAPTION_ON_BUTTONS = ("Turn on captions", "Show captions")
CAPTION_OFF_BUTTONS = ("Turn off captions", "Hide captions")
INCALL_CONTROLS_CSS = (
    'button[aria-label*="Turn on captions" i], '
    'button[aria-label*="Turn off captions" i], '
    'button[aria-label*="Show captions" i], '
    'button[aria-label*="Hide captions" i], '
    '[role="button"][aria-label*="Turn on captions" i], '
    '[role="button"][aria-label*="Turn off captions" i], '
    '[role="button"][aria-label*="Show captions" i], '
    '[role="button"][aria-label*="Hide captions" i], '
    'button[aria-label*="Chat with everyone" i], '
    '[role="button"][aria-label*="Chat with everyone" i], '
    'button[aria-label*="In-call messages" i], '
    '[role="button"][aria-label*="In-call messages" i], '
    'button[aria-label*="Show everyone" i], '
    'button[aria-label="People" i], '
    'button[aria-label*="Raise hand" i], '
    'button[aria-label*="Send a reaction" i], '
    'button[aria-label*="Meeting details" i]'
)
CHAT_BOX = "Send a message to everyone"
CHAT_BUTTON = "Chat with everyone"
LEAVE_CALL = "Leave call"
JOIN_BUTTONS = ("Join now", "Ask to join", "Switch here", "Rejoin")
DISMISS_BUTTONS = (
    "Continue without microphone and camera",
    "Continue without camera",
    "Continue without microphone",
    "Not now",
    "Dismiss",
    "Got it",
)
LOBBY_TEXTS = (
    "Please wait until a meeting host brings you into the call",
    "Asking to be let in",
    "Wait until a meeting host brings you into the call",
    "Someone will let you in soon",
    "Waiting for the host to let you in",
    "Getting ready",
    "You'll be able to join in just a moment",
)
REJECTED_TEXTS = (
    "You can't join this video call",
    "Someone in the call denied your request to join",
    "You've been denied entry",
)
MIC_ON_ACTION = "Turn off microphone"
CAM_ON_ACTION = "Turn off camera"
MIC_OFF_STATE = "Turn on microphone"
CAM_OFF_STATE = "Turn on camera"
NAME_LABEL = "Your name"
MORE_OPTIONS = "More options"
SETTINGS_ITEM = "Settings"
VIDEO_TAB = "Video"
RECEIVE_RESOLUTION = "Receive resolution"
AUDIO_ONLY = "Audio only"
PARTICIPANT = "[data-participant-id]"
PARTICIPANT_SELF = "[data-participant-id][data-self='true']"
ENDED = "[data-ba-ended='true']"
