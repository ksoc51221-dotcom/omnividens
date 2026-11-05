"""Errors raised by the video pipeline."""


class VideoError(Exception):
    """Base class. `user_message` is safe to show in Telegram."""

    user_message = "I couldn't process that video."
    # Wording for the cloud routine, where "try another network" makes no sense. Defaults to user_message.
    cloud_message: str | None = None


class DownloadError(VideoError):
    """The link could not be downloaded and we don't know why more precisely."""

    user_message = (
        "I couldn't download that video, and the site gave no clear reason. "
        "Try sending the video file itself."
    )


class BlockedError(DownloadError):
    """The site's bot check refused us (YouTube: "confirm you're not a bot")."""

    user_message = (
        "The site's bot check blocked this download from here, so I can't fetch it. "
        "Try again later, or send the video file itself."
    )
    cloud_message = (
        "YouTube blocks downloads from the cloud, so I can't fetch this link from there."
    )


class RateLimitedError(DownloadError):
    """The site answered HTTP 429 (too many requests) for the video itself."""

    user_message = (
        "The site is rate limiting downloads from here right now (too many requests). "
        "Try again in a while, or send the video file itself."
    )


class BadLinkError(DownloadError):
    """The video is private, removed, members-only or from a site we can't read."""

    user_message = (
        "That link is private, removed or not supported, so I can't read it. "
        "Check that it opens for anyone, or send the video file itself."
    )


class NetworkError(DownloadError):
    """We couldn't reach the host (DNS, SSL, blocked network, timeout)."""

    def __init__(self, message: str, host: str = ""):
        super().__init__(message)
        self.host = host
        where = host or "that site"
        self.user_message = (
            f"I couldn't reach {where} from this network. "
            "Try again on another network (e.g. phone hotspot), or send the video file itself."
        )
        self.cloud_message = f"I couldn't reach {where} from the cloud. Try again later."


class FrameError(VideoError):
    """ffmpeg could not read frames from the file."""

    user_message = "I couldn't read that video file. Try sending it as an mp4."


class TooLongError(DownloadError):
    """Video is over the size or length cap."""

    user_message = (
        "That video is too long or too big for me (max 10 minutes). "
        "Send a shorter clip or just the key part."
    )


class ToolTimeoutError(VideoError):
    """ffmpeg/ffprobe took too long (hung or a huge file)."""

    user_message = "Processing that video took too long. Try a shorter clip."
