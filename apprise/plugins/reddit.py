# BSD 2-Clause License
#
# Apprise - Push Notification Library.
# Copyright (c) 2025, Chris Caron <lead2gold@gmail.com>
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

# 1. Visit https://www.reddit.com/prefs/apps and scroll to the bottom
# 2. Click on the button that reads 'are you a developer? create an app...'
# 3. Set the mode to `script`,
# 4. Provide a `name`, `description`, `redirect uri` and save it.
# 5. Once the bot is saved, you'll be given a ID (next to the the bot name)
#    and a Secret.

# The App ID will look something like this: YWARPXajkk645m
# The App Secret will look something like this: YZGKc5YNjq3BsC-bf7oBKalBMeb1xA
# The App will also have a location where you can identify the users
# who have access (identified as Developers) to the app itself. You will
# additionally need these credentials authenticate with.

# With this information you'll be able to form the URL:
# reddit://{user}:{password}@{app_id}/{app_secret}

# All of the documentation needed to work with the Reddit API can be found
# here:
#   - https://www.reddit.com/dev/api/
#   - https://www.reddit.com/dev/api/#POST_api_submit
#   - https://github.com/reddit-archive/reddit/wiki/API
from datetime import datetime, timedelta, timezone
from json import loads

import requests

from .. import __title__, __version__
from ..common import NotifyFormat, NotifyType
from ..locale import gettext_lazy as _
from ..url import PrivacyMode
from ..utils.parse import parse_bool, parse_list, validate_regex
from .base import NotifyBase

# Extend HTTP Error Messages
REDDIT_HTTP_ERROR_MAP = {
    401: "Unauthorized - Invalid Token",
}


class RedditMessageKind:
    """Define the kinds of messages supported."""

    # Attempt to auto-detect the type prior to passing along the message to
    # Reddit
    AUTO = "auto"

    # A common message
    SELF = "self"

    # A Hyperlink
    LINK = "link"


REDDIT_MESSAGE_KINDS = (
    RedditMessageKind.AUTO,
    RedditMessageKind.SELF,
    RedditMessageKind.LINK,
)


class NotifyReddit(NotifyBase):
    """A wrapper for Notify Reddit Notifications."""

    # The default descriptive name associated with the Notification
    service_name = "Reddit"

    # The services URL
    service_url = "https://reddit.com"

    # The default secure protocol
    secure_protocol = "reddit"

    # A URL that takes you to the setup/help of the specific protocol
    setup_url = "https://github.com/caronc/apprise/wiki/Notify_reddit"

    # The maximum size of the message
    body_maxlen = 6000

    # Maximum title length as defined by the Reddit API
    title_maxlen = 300

    # Default to markdown
    notify_format = NotifyFormat.MARKDOWN

    # The default Notification URL to use
    auth_url = "https://www.reddit.com/api/v1/access_token"
    submit_url = "https://oauth.reddit.com/api/submit"

    # Reddit is kind enough to return how many more requests we're allowed to
    # continue to make within it's header response as:
    # X-RateLimit-Reset: The epoc time (in seconds) we can expect our
    #                    rate-limit to be reset.
    # X-RateLimit-Remaining: an integer identifying how many requests we're
    #                        still allow to make.
    request_rate_per_sec = 0

    # Taken right from google.auth.helpers:
    clock_skew = timedelta(seconds=10)

    # 1 hour in seconds (the lifetime of our token)
    access_token_lifetime_sec = timedelta(seconds=3600)

    # Define object templates
    templates = (
        "{schema}://{user}:{password}@{app_id}/{app_secret}/{targets}",
    )

    # Define our template arguments
    template_tokens = dict(
        NotifyBase.template_tokens,
        **{
            "user": {
                "name": _("User Name"),
                "type": "string",
                "required": True,
            },
            "password": {
                "name": _("Password"),
                "type": "string",
                "private": True,
                "required": True,
            },
            "app_id": {
                "name": _("Application ID"),
                "type": "string",
                "private": True,
                "required": True,
                "regex": (r"^[a-z0-9_-]+$", "i"),
            },
            "app_secret": {
                "name": _("Application Secret"),
                "type": "string",
                "private": True,
                "required": True,
                "regex": (r"^[a-z0-9_-]+$", "i"),
            },
            "target_subreddit": {
                "name": _("Target Subreddit"),
                "type": "string",
                "map_to": "targets",
            },
            "targets": {
                "name": _("Targets"),
                "type": "list:string",
                "required": True,
            },
        },
    )

    # Define our template arguments
    template_args = dict(
        NotifyBase.template_args,
        **{
            "to": {
                "alias_of": "targets",
            },
            "kind": {
                "name": _("Kind"),
                "type": "choice:string",
                "values": REDDIT_MESSAGE_KINDS,
                "default": RedditMessageKind.AUTO,
            },
            "flair_id": {
                "name": _("Flair ID"),
                "type": "string",
                "map_to": "flair_id",
            },
            "flair_text": {
                "name": _("Flair Text"),
                "type": "string",
                "map_to": "flair_text",
            },
            "nsfw": {
                "name": _("NSFW"),
                "type": "bool",
                "default": False,
                "map_to": "nsfw",
            },
            "ad": {
                "name": _("Is Ad?"),
                "type": "bool",
                "default": False,
                "map_to": "advertisement",
            },
            "replies": {
                "name": _("Send Replies"),
                "type": "bool",
                "default": True,
                "map_to": "sendreplies",
            },
            "spoiler": {
                "name": _("Is Spoiler"),
                "type": "bool",
                "default": False,
                "map_to": "spoiler",
            },
            "resubmit": {
                "name": _("Resubmit Flag"),
                "type": "bool",
                "default": False,
                "map_to": "resubmit",
            },
        },
    )

    def __init__(
        self,
        app_id=None,
        app_secret=None,
        targets=None,
        kind=None,
        nsfw=False,
        sendreplies=True,
        resubmit=False,
        spoiler=False,
        advertisement=False,
        flair_id=None,
        flair_text=None,
        **kwargs,
    ):
        """Initialize Notify Reddit Object."""
        super().__init__(**kwargs)

        # Initialize subreddit list
        self.subreddits = set()

        # Not Safe For Work Flag
        self.nsfw = nsfw

        # Send Replies Flag
        self.sendreplies = sendreplies

        # Is Spoiler Flag
        self.spoiler = spoiler

        # Resubmit Flag
        self.resubmit = resubmit

        # Is Ad?
        self.advertisement = advertisement

        # Flair details
        self.flair_id = flair_id
        self.flair_text = flair_text

        # Our keys we build using the provided content
        self.__refresh_token = None
        self.__access_token = None
        self.__access_token_expiry = datetime.now(timezone.utc)

        self.kind = (
            kind.strip().lower()
            if isinstance(kind, str)
            else self.template_args["kind"]["default"]
        )

        if self.kind not in REDDIT_MESSAGE_KINDS:
            msg = f"An invalid Reddit message kind ({kind}) was specified"
            self.logger.warning(msg)
            raise TypeError(msg)

        self.user = validate_regex(self.user)
        if not self.user:
            msg = f"An invalid Reddit User ID ({self.user}) was specified"
            self.logger.warning(msg)
            raise TypeError(msg)

        self.password = validate_regex(self.password)
        if not self.password:
            msg = f"An invalid Reddit Password ({self.password}) was specified"
            self.logger.warning(msg)
            raise TypeError(msg)

        self.client_id = validate_regex(
            app_id, *self.template_tokens["app_id"]["regex"]
        )
        if not self.client_id:
            msg = f"An invalid Reddit App ID ({app_id}) was specified"
            self.logger.warning(msg)
            raise TypeError(msg)

        self.client_secret = validate_regex(
            app_secret, *self.template_tokens["app_secret"]["regex"]
        )
        if not self.client_secret:
            msg = f"An invalid Reddit App Secret ({app_secret}) was specified"
            self.logger.warning(msg)
            raise TypeError(msg)

        # Build list of subreddits
        self.subreddits = [
            sr.lstrip("#") for sr in parse_list(targets) if sr.lstrip("#")
        ]

        if not self.subreddits:
            self.logger.warning("No subreddits were identified to be notified")

        # For Rate Limit Tracking Purposes
        self.ratelimit_reset = datetime.now(timezone.utc).replace(tzinfo=None)

        # Default to 1.0
        self.ratelimit_remaining = 1.0

        return

    @property
    def url_identifier(self):
        """Returns all of the identifiers that make this URL unique from
        another simliar one.

        Targets or end points should never be identified here.
        """
        return (
            self.secure_protocol,
            self.client_id,
            self.client_secret,
            self.user,
            self.password,
        )

    def url(self, privacy=False, *args, **kwargs):
        """Returns the URL built dynamically based on specified arguments."""

        # Define any URL parameters
        params = {
            "kind": self.kind,
            "ad": "yes" if self.advertisement else "no",
            "nsfw": "yes" if self.nsfw else "no",
            "resubmit": "yes" if self.resubmit else "no",
            "replies": "yes" if self.sendreplies else "no",
            "spoiler": "yes" if self.spoiler else "no",
        }

        # Flair support
        if self.flair_id:
            params["flair_id"] = self.flair_id

        if self.flair_text:
            params["flair_text"] = self.flair_text

        # Extend our parameters
        params.update(self.url_parameters(privacy=privacy, *args, **kwargs))

        return (
            "{schema}://{user}:{password}@{app_id}/{app_secret}"
            "/{targets}/?{params}".format(
                schema=self.secure_protocol,
                user=NotifyReddit.quote(self.user, safe=""),
                password=self.pprint(
                    self.password, privacy, mode=PrivacyMode.Secret, safe=""
                ),
                app_id=self.pprint(
                    self.client_id, privacy, mode=PrivacyMode.Secret, safe=""
                ),
                app_secret=self.pprint(
                    self.client_secret,
                    privacy,
                    mode=PrivacyMode.Secret,
                    safe="",
                ),
                targets="/".join(
                    [NotifyReddit.quote(x, safe="") for x in self.subreddits]
                ),
                params=NotifyReddit.urlencode(params),
            )
        )

    def __len__(self):
        """Returns the number of targets associated with this notification."""
        return len(self.subreddits)

    def login(self):
        """A simple wrapper to authenticate with the Reddit Server."""

        # Prepare our payload
        payload = {
            "grant_type": "password",
            "username": self.user,
            "password": self.password,
        }

        # Enforce a False flag setting before calling _fetch()
        self.__access_token = False

        # Send Login Information
        postokay, response = self._fetch(
            self.auth_url,
            payload=payload,
        )

        if not postokay or not response:
            # Setting this variable to False as a way of letting us know
            # we failed to authenticate on our last attempt
            self.__access_token = False
            return False

        # Our response object looks like this (content has been altered for
        # presentation purposes):
        # {
        #     "access_token": Your access token,
        #     "token_type": "bearer",
        #     "expires_in": Unix Epoch Seconds,
        #     "scope": A scope string,
        #     "refresh_token": Your refresh token
        # }

        # Acquire our token
        self.__access_token = response.get("access_token")

        # Handle other optional arguments we can use
        if "expires_in" in response:
            delta = timedelta(seconds=int(response["expires_in"]))
            self.__access_token_expiry = (
                delta + datetime.now(timezone.utc) - self.clock_skew
            )
        else:
            self.__access_token_expiry = (
                self.access_token_lifetime_sec
                + datetime.now(timezone.utc)
                - self.clock_skew
            )

        # The Refresh Token
        self.__refresh_token = response.get(
            "refresh_token", self.__refresh_token
        )

        if self.__access_token:
            self.logger.info(f"Authenticated to Reddit as {self.user}")
            return True

        self.logger.warning(f"Failed to authenticate to Reddit as {self.user}")

        # Mark our failure
        return False

    def send(self, body, title="", notify_type=NotifyType.INFO, **kwargs):
        """Perform Reddit Notification."""

        # error tracking (used for function return)
        has_error = False

        if not self.__access_token and not self.login():
            # We failed to authenticate - we're done
            return False

        if not len(self.subreddits):
            # We have nothing to notify; we're done
            self.logger.warning("There are no Reddit targets to notify")
            return False

        # Prepare our Message Type/Kind
        if self.kind == RedditMessageKind.AUTO:
            parsed = NotifyBase.parse_url(body)
            # Detect a link
            if (
                parsed
                and parsed.get("schema", "").startswith("http")
                and parsed.get("host")
            ):
                kind = RedditMessageKind.LINK

            else:
                kind = RedditMessageKind.SELF
        else:
            kind = self.kind

        # Create a copy of the subreddits list
        subreddits = list(self.subreddits)
        while len(subreddits) > 0:
            # Retrieve our subreddit
            subreddit = subreddits.pop()

            # Prepare our payload
            payload = {
                "ad": bool(self.advertisement),
                "api_type": "json",
                "extension": "json",
                "sr": subreddit,
                "title": title if title else self.app_desc,
                "kind": kind,
                "nsfw": bool(self.nsfw),
                "resubmit": bool(self.resubmit),
                "sendreplies": bool(self.sendreplies),
                "spoiler": bool(self.spoiler),
            }

            if self.flair_id:
                payload["flair_id"] = self.flair_id

            if self.flair_text:
                payload["flair_text"] = self.flair_text

            if kind == RedditMessageKind.LINK:
                payload.update({
                    "url": body,
                })
            else:
                payload.update({
                    "text": body,
                })

            postokay, response = self._fetch(self.submit_url, payload=payload)
            # only toggle has_error flag if we had an error
            if not postokay:
                # Mark our failure
                has_error = True
                continue

            # If we reach here, we were successful
            self.logger.info(f"Sent Reddit notification to {subreddit}")

        return not has_error

    def _fetch(self, url, payload=None):
        """Wrapper to Reddit API requests object."""

        # use what was specified, otherwise build headers dynamically
        headers = {"User-Agent": f"{__title__} v{__version__}"}

        if self.__access_token:
            # Set our token
            headers["Authorization"] = f"Bearer {self.__access_token}"

        # Prepare our url
        url = self.submit_url if self.__access_token else self.auth_url

        # Some Debug Logging
        self.logger.debug(
            f"Reddit POST URL: {url} (cert_verify={self.verify_certificate})"
        )
        self.logger.debug(f"Reddit Payload: {payload!s}")

        # By default set wait to None
        wait = None

        if self.ratelimit_remaining <= 0.0:
            # Determine how long we should wait for or if we should wait at
            # all. This isn't fool-proof because we can't be sure the client
            # time (calling this script) is completely synced up with the
            # Reddit server.  One would hope we're on NTP and our clocks are
            # the same allowing this to role smoothly:

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if now < self.ratelimit_reset:
                # We need to throttle for the difference in seconds
                wait = abs(
                    (
                        self.ratelimit_reset - now + self.clock_skew
                    ).total_seconds()
                )

        # Always call throttle before any remote server i/o is made;
        self.throttle(wait=wait)

        # Initialize a default value for our content value
        content = {}

        # acquire our request mode
        try:
            r = requests.post(
                url,
                data=payload,
                auth=(
                    None
                    if self.__access_token
                    else (self.client_id, self.client_secret)
                ),
                headers=headers,
                verify=self.verify_certificate,
                timeout=self.request_timeout,
            )

            #  We attempt to login again and retry the original request
            #  if we aren't in the process of handling a login already
            if (
                r.status_code != requests.codes.ok
                and self.__access_token
                and url != self.auth_url
            ):

                # We had a problem
                status_str = NotifyReddit.http_response_code_lookup(
                    r.status_code, REDDIT_HTTP_ERROR_MAP
                )

                self.logger.debug(
                    "Taking countermeasures after failed to send to Reddit "
                    "{}: {}error={}".format(
                        url, ", " if status_str else "", r.status_code
                    )
                )

                self.logger.debug(f"Response Details:\r\n{r.content}")

                # We failed to authenticate with our token; login one more
                # time and retry this original request
                if not self.login():
                    return (False, {})

                # Try again
                r = requests.post(
                    url,
                    data=payload,
                    headers=headers,
                    verify=self.verify_certificate,
                    timeout=self.request_timeout,
                )

            # Get our JSON content if it's possible
            try:
                content = loads(r.content)

            except (TypeError, ValueError, AttributeError):
                # TypeError = r.content is not a String
                # ValueError = r.content is Unparsable
                # AttributeError = r.content is None

                # We had a problem
                status_str = NotifyReddit.http_response_code_lookup(
                    r.status_code, REDDIT_HTTP_ERROR_MAP
                )

                # Reddit always returns a JSON response
                self.logger.warning(
                    "Failed to send to Reddit after countermeasures {}: "
                    "{}error={}".format(
                        url, ", " if status_str else "", r.status_code
                    )
                )

                self.logger.debug(f"Response Details:\r\n{r.content}")
                return (False, {})

            if r.status_code != requests.codes.ok:
                # We had a problem
                status_str = NotifyReddit.http_response_code_lookup(
                    r.status_code, REDDIT_HTTP_ERROR_MAP
                )

                self.logger.warning(
                    "Failed to send to Reddit {}: {}error={}".format(
                        url, ", " if status_str else "", r.status_code
                    )
                )

                self.logger.debug(f"Response Details:\r\n{r.content}")

                # Mark our failure
                return (False, content)

            errors = (
                []
                if not content
                else content.get("json", {}).get("errors", [])
            )
            if errors:
                self.logger.warning(
                    f"Failed to send to Reddit {url}: {errors!s}"
                )

                self.logger.debug(f"Response Details:\r\n{r.content}")

                # Mark our failure
                return (False, content)

            try:
                # Store our rate limiting (if provided)
                self.ratelimit_remaining = float(
                    r.headers.get("X-RateLimit-Remaining")
                )
                self.ratelimit_reset = datetime.fromtimestamp(
                    int(r.headers.get("X-RateLimit-Reset")), timezone.utc
                ).replace(tzinfo=None)

            except (TypeError, ValueError):
                # This is returned if we could not retrieve this information
                # gracefully accept this state and move on
                pass

        except requests.RequestException as e:
            self.logger.warning(
                f"Exception received when sending Reddit to {url}"
            )
            self.logger.debug(f"Socket Exception: {e!s}")

            # Mark our failure
            return (False, content)

        return (True, content)

    @staticmethod
    def parse_url(url):
        """Parses the URL and returns enough arguments that can allow us to re-
        instantiate this object."""
        results = NotifyBase.parse_url(url, verify_host=False)
        if not results:
            # We're done early as we couldn't load the results
            return results

        # Acquire our targets
        results["targets"] = NotifyReddit.split_path(results["fullpath"])

        # Kind override
        if "kind" in results["qsd"] and results["qsd"]["kind"]:
            results["kind"] = NotifyReddit.unquote(
                results["qsd"]["kind"].strip().lower()
            )
        else:
            results["kind"] = RedditMessageKind.AUTO

        # Is an Ad?
        results["ad"] = parse_bool(results["qsd"].get("ad", False))

        # Get Not Safe For Work (NSFW) Flag
        results["nsfw"] = parse_bool(results["qsd"].get("nsfw", False))

        # Send Replies Flag
        results["replies"] = parse_bool(results["qsd"].get("replies", True))

        # Resubmit Flag
        results["resubmit"] = parse_bool(results["qsd"].get("resubmit", False))

        # Is Spoiler Flag
        results["spoiler"] = parse_bool(results["qsd"].get("spoiler", False))

        if "flair_text" in results["qsd"]:
            results["flair_text"] = NotifyReddit.unquote(
                results["qsd"]["flair_text"]
            )

        if "flair_id" in results["qsd"]:
            results["flair_id"] = NotifyReddit.unquote(
                results["qsd"]["flair_id"]
            )

        # The 'to' makes it easier to use yaml configuration
        if "to" in results["qsd"] and len(results["qsd"]["to"]):
            results["targets"] += NotifyReddit.parse_list(results["qsd"]["to"])

        if "app_id" in results["qsd"]:
            results["app_id"] = NotifyReddit.unquote(results["qsd"]["app_id"])
        else:
            # The App/Bot ID is the hostname
            results["app_id"] = NotifyReddit.unquote(results["host"])

        if "app_secret" in results["qsd"]:
            results["app_secret"] = NotifyReddit.unquote(
                results["qsd"]["app_secret"]
            )
        else:
            # The first target identified is the App secret
            results["app_secret"] = (
                None if not results["targets"] else results["targets"].pop(0)
            )

        return results
