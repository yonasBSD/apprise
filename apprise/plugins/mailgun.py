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

# Signup @ https://www.mailgun.com/
#
# Each domain will have an API key associated with it. If you sign up you'll
# get a sandbox domain to use.  Or if you set up your own, they'll have
# api keys associated with them too.  Find your API key out by visiting
#    https://app.mailgun.com/app/domains
#
# From here you can click on the domain you're interested in. You can acquire
# the API Key from here which will look something like:
#    4b4f2918c6c21ba0a26ad2af73c07f4d-dk5f51da-8f91a0df
#
# You'll also need to know the domain that is associated with your API key.
# This will be obvious with a paid account because it will be the domain name
# you've registered with them.   But if you're using a test account, it will
# be name of the sandbox you've set up such as:
#    sandbox74bda3414c06kb5acb946.mailgun.org
#
# Knowing this, you can buid your mailgun url as follows:
#  mailgun://{user}@{domain}/{apikey}
#  mailgun://{user}@{domain}/{apikey}/{email}
#
# You can email as many addresses as you want as:
#  mailgun://{user}@{domain}/{apikey}/{email1}/{email2}/{emailN}
#
#  The {user}@{domain} effectively assembles the 'from' email address
#  the email will be transmitted from.  If no email address is specified
#  then it will also become the 'to' address as well.
#
from email.utils import formataddr

import requests

from ..common import NotifyFormat, NotifyType
from ..locale import gettext_lazy as _
from ..logger import logger
from ..utils.parse import is_email, parse_bool, parse_emails, validate_regex
from .base import NotifyBase

# Provide some known codes Mailgun uses and what they translate to:
# Based on https://documentation.mailgun.com/en/latest/api-intro.html#errors
MAILGUN_HTTP_ERROR_MAP = {
    400: "A bad request was made to the server.",
    401: "The provided API Key was not valid.",
    402: "The request failed for a reason out of your control.",
    404: "The requested API query is not valid.",
    413: "Provided attachment is to big.",
}


# Priorities
class MailgunRegion:
    US = "us"
    EU = "eu"


# Mailgun APIs
MAILGUN_API_LOOKUP = {
    MailgunRegion.US: "https://api.mailgun.net/v3/",
    MailgunRegion.EU: "https://api.eu.mailgun.net/v3/",
}

# A List of our regions we can use for verification
MAILGUN_REGIONS = (
    MailgunRegion.US,
    MailgunRegion.EU,
)


class NotifyMailgun(NotifyBase):
    """A wrapper for Mailgun Notifications."""

    # The default descriptive name associated with the Notification
    service_name = "Mailgun"

    # The services URL
    service_url = "https://www.mailgun.com/"

    # All notification requests are secure
    secure_protocol = "mailgun"

    # Mailgun advertises they allow 300 requests per minute.
    # 60/300 = 0.2
    request_rate_per_sec = 0.20

    # A URL that takes you to the setup/help of the specific protocol
    setup_url = "https://github.com/caronc/apprise/wiki/Notify_mailgun"

    # Support attachments
    attachment_support = True

    # Default Notify Format
    notify_format = NotifyFormat.HTML

    # The maximum amount of emails that can reside within a single
    # batch transfer
    default_batch_size = 2000

    # Define object templates
    templates = (
        "{schema}://{user}@{host}:{apikey}/",
        "{schema}://{user}@{host}:{apikey}/{targets}",
    )

    # Define our template tokens
    template_tokens = dict(
        NotifyBase.template_tokens,
        **{
            "user": {
                "name": _("User Name"),
                "type": "string",
                "required": True,
            },
            "host": {
                "name": _("Domain"),
                "type": "string",
                "required": True,
            },
            "apikey": {
                "name": _("API Key"),
                "type": "string",
                "private": True,
                "required": True,
            },
            "target_email": {
                "name": _("Target Email"),
                "type": "string",
                "map_to": "targets",
            },
            "targets": {
                "name": _("Targets"),
                "type": "list:string",
            },
        },
    )

    # Define our template arguments
    template_args = dict(
        NotifyBase.template_args,
        **{
            "name": {
                "name": _("From Name"),
                "type": "string",
                "map_to": "from_addr",
            },
            "from": {
                "alias_of": "name",
            },
            "region": {
                "name": _("Region Name"),
                "type": "choice:string",
                "values": MAILGUN_REGIONS,
                "default": MailgunRegion.US,
                "map_to": "region_name",
            },
            "to": {
                "alias_of": "targets",
            },
            "cc": {
                "name": _("Carbon Copy"),
                "type": "list:string",
            },
            "bcc": {
                "name": _("Blind Carbon Copy"),
                "type": "list:string",
            },
            "batch": {
                "name": _("Batch Mode"),
                "type": "bool",
                "default": False,
            },
        },
    )

    # Define any kwargs we're using
    template_kwargs = {
        "headers": {
            "name": _("Email Header"),
            "prefix": "+",
        },
        "tokens": {
            "name": _("Template Tokens"),
            "prefix": ":",
        },
    }

    def __init__(
        self,
        apikey,
        targets,
        cc=None,
        bcc=None,
        from_addr=None,
        region_name=None,
        headers=None,
        tokens=None,
        batch=False,
        **kwargs,
    ):
        """Initialize Mailgun Object."""
        super().__init__(**kwargs)

        # API Key (associated with project)
        self.apikey = validate_regex(apikey)
        if not self.apikey:
            msg = f"An invalid Mailgun API Key ({apikey}) was specified."
            self.logger.warning(msg)
            raise TypeError(msg)

        # Validate our username
        if not self.user:
            msg = "No Mailgun username was specified."
            self.logger.warning(msg)
            raise TypeError(msg)

        # Acquire Email 'To'
        self.targets = []

        # Acquire Carbon Copies
        self.cc = set()

        # Acquire Blind Carbon Copies
        self.bcc = set()

        # For tracking our email -> name lookups
        self.names = {}

        self.headers = {}
        if headers:
            # Store our extra headers
            self.headers.update(headers)

        self.tokens = {}
        if tokens:
            # Store our template tokens
            self.tokens.update(tokens)

        # Prepare Batch Mode Flag
        self.batch = batch

        # Store our region
        try:
            self.region_name = (
                NotifyMailgun.template_args["region"]["default"]
                if region_name is None
                else region_name.lower()
            )

            if self.region_name not in MAILGUN_REGIONS:
                # allow the outer except to handle this common response
                raise
        except:
            # Invalid region specified
            msg = f"The Mailgun region specified ({region_name}) is invalid."
            self.logger.warning(msg)
            raise TypeError(msg) from None

        # Get our From username (if specified)
        self.from_addr = [self.app_id, f"{self.user}@{self.host}"]

        if from_addr:
            result = is_email(from_addr)
            if result:
                self.from_addr = (
                    result["name"] if result["name"] else False,
                    result["full_email"],
                )
            else:
                self.from_addr[0] = from_addr

        if not is_email(self.from_addr[1]):
            # Parse Source domain based on from_addr
            msg = f"Invalid ~From~ email format: {self.from_addr}"
            self.logger.warning(msg)
            raise TypeError(msg)

        if targets:
            # Validate recipients (to:) and drop bad ones:
            for recipient in parse_emails(targets):
                result = is_email(recipient)
                if result:
                    self.targets.append((
                        result["name"] if result["name"] else False,
                        result["full_email"],
                    ))
                    continue

                self.logger.warning(
                    f"Dropped invalid To email ({recipient}) specified.",
                )

        else:
            # If our target email list is empty we want to add ourselves to it
            self.targets.append((False, self.from_addr[1]))

        # Validate recipients (cc:) and drop bad ones:
        for recipient in parse_emails(cc):
            email = is_email(recipient)
            if email:
                self.cc.add(email["full_email"])

                # Index our name (if one exists)
                self.names[email["full_email"]] = (
                    email["name"] if email["name"] else False
                )
                continue

            self.logger.warning(
                f"Dropped invalid Carbon Copy email ({recipient}) specified.",
            )

        # Validate recipients (bcc:) and drop bad ones:
        for recipient in parse_emails(bcc):
            email = is_email(recipient)
            if email:
                self.bcc.add(email["full_email"])

                # Index our name (if one exists)
                self.names[email["full_email"]] = (
                    email["name"] if email["name"] else False
                )
                continue

            self.logger.warning(
                "Dropped invalid Blind Carbon Copy email "
                f"({recipient}) specified.",
            )

    def send(
        self,
        body,
        title="",
        notify_type=NotifyType.INFO,
        attach=None,
        **kwargs,
    ):
        """Perform Mailgun Notification."""

        if not self.targets:
            # There is no one to email; we're done
            self.logger.warning("There are no Email recipients to notify")
            return False

        # error tracking (used for function return)
        has_error = False

        # Send in batches if identified to do so
        batch_size = 1 if not self.batch else self.default_batch_size

        # Prepare our headers
        headers = {
            "User-Agent": self.app_id,
            "Accept": "application/json",
        }

        # Track our potential files
        files = {}

        if attach and self.attachment_support:
            for idx, attachment in enumerate(attach):
                # Perform some simple error checking
                if not attachment:
                    # We could not access the attachment
                    self.logger.error(
                        "Could not access attachment"
                        f" {attachment.url(privacy=True)}."
                    )
                    return False

                self.logger.debug(
                    "Preparing Mailgun attachment"
                    f" {attachment.url(privacy=True)}"
                )

                # Prepare our filename
                filename = (
                    attachment.name
                    if attachment.name
                    else f"file{idx + 1:03}.dat"
                )

                try:
                    files[f"attachment[{idx}]"] = (
                        filename,
                        # file handle is safely closed through this code
                        # ignoring of SIM115 is intentional
                        open(attachment.path, "rb"),  # noqa: SIM115
                    )

                except OSError as e:
                    self.logger.warning(
                        "An I/O error occurred while opening {}.".format(
                            attachment.name if attachment else "attachment"
                        )
                    )
                    self.logger.debug(f"I/O Exception: {e!s}")

                    # tidy up any open files before we make our early
                    # return
                    for entry in files.values():
                        self.logger.trace(f"Closing attachment {entry[0]}")
                        entry[1].close()

                    return False

        reply_to = formataddr(self.from_addr, charset="utf-8")

        # Prepare our payload
        payload = {
            # pass skip-verification switch upstream too
            "o:skip-verification": not self.verify_certificate,
            # Base payload options
            "from": reply_to,
            "subject": title,
        }

        if self.notify_format == NotifyFormat.HTML:
            payload["html"] = body

        else:
            payload["text"] = body

        # Prepare our URL as it's based on our hostname
        url = f"{MAILGUN_API_LOOKUP[self.region_name]}{self.host}/messages"

        # Create a copy of the targets list
        emails = list(self.targets)

        for index in range(0, len(emails), batch_size):
            # Initialize our cc list
            cc = self.cc - self.bcc

            # Initialize our bcc list
            bcc = set(self.bcc)

            # Initialize our to list
            to = []

            # Ensure we're pointed to the head of the attachment; this doesn't
            # do much for the first iteration through this loop as we're
            # already pointing there..., but it allows us to re-use the
            # attachment over and over again without closing and then
            # re-opening the same file again and again
            for entry in files.values():
                try:
                    self.logger.trace(
                        f"Seeking to head of attachment {entry[0]}"
                    )
                    entry[1].seek(0)

                except OSError as e:
                    self.logger.warning(
                        "An I/O error occurred seeking to head of attachment "
                        f"{entry[0]}."
                    )
                    self.logger.debug(f"I/O Exception: {e!s}")

                    # tidy up any open files before we make our early
                    # return
                    for entry in files.values():
                        self.logger.trace(f"Closing attachment {entry[0]}")
                        entry[1].close()

                    return False

            for to_addr in self.targets[index : index + batch_size]:
                # Strip target out of cc list if in To
                cc = cc - {to_addr[1]}

                # Strip target out of bcc list if in To
                bcc = bcc - {to_addr[1]}

                # Prepare our `to`
                to.append(formataddr(to_addr, charset="utf-8"))

            # Prepare our To
            payload["to"] = ",".join(to)

            if cc:
                # Format our cc addresses to support the Name field
                payload["cc"] = ",".join([
                    formataddr(
                        (self.names.get(addr, False), addr),
                        charset="utf-8",
                    )
                    for addr in cc
                ])

            # Format our bcc addresses to support the Name field
            if bcc:
                payload["bcc"] = ",".join(bcc)

            # Store our token entries; users can reference these as %value%
            # in their email message.
            if self.tokens:
                payload.update({f"v:{k}": v for k, v in self.tokens.items()})

            # Store our header entries if defined into the payload
            # in their payload
            if self.headers:
                payload.update({f"h:{k}": v for k, v in self.headers.items()})

            # Some Debug Logging
            self.logger.debug(
                "Mailgun POST URL:"
                f" {url} (cert_verify={self.verify_certificate})"
            )
            self.logger.debug(f"Mailgun Payload: {payload}")

            # For logging output of success and errors; we get a head count
            # of our outbound details:
            verbose_dest = (
                ", ".join(
                    [x[1] for x in self.targets[index : index + batch_size]]
                )
                if len(self.targets[index : index + batch_size]) <= 3
                else (
                    f"{len(self.targets[index:index + batch_size])} recipients"
                )
            )

            # Always call throttle before any remote server i/o is made
            self.throttle()
            try:
                r = requests.post(
                    url,
                    auth=("api", self.apikey),
                    data=payload,
                    headers=headers,
                    files=files if files else None,
                    verify=self.verify_certificate,
                    timeout=self.request_timeout,
                )

                if r.status_code != requests.codes.ok:
                    # We had a problem
                    status_str = NotifyBase.http_response_code_lookup(
                        r.status_code, MAILGUN_HTTP_ERROR_MAP
                    )

                    self.logger.warning(
                        "Failed to send Mailgun notification to {}: "
                        "{}{}error={}.".format(
                            verbose_dest,
                            status_str,
                            ", " if status_str else "",
                            r.status_code,
                        )
                    )

                    self.logger.debug(f"Response Details:\r\n{r.content}")

                    # Mark our failure
                    has_error = True
                    continue

                else:
                    self.logger.info(
                        f"Sent Mailgun notification to {verbose_dest}."
                    )

            except requests.RequestException as e:
                self.logger.warning(
                    "A Connection error occurred sending"
                    f" Mailgun:{verbose_dest} "
                    + "notification."
                )
                self.logger.debug(f"Socket Exception: {e!s}")

                # Mark our failure
                has_error = True
                continue

            except OSError as e:
                self.logger.warning(
                    "An I/O error occurred while reading attachments"
                )
                self.logger.debug(f"I/O Exception: {e!s}")

                # Mark our failure
                has_error = True
                continue

        # Close any potential attachments that are still open
        for entry in files.values():
            self.logger.trace(f"Closing attachment {entry[0]}")
            entry[1].close()

        return not has_error

    @property
    def url_identifier(self):
        """Returns all of the identifiers that make this URL unique from
        another simliar one.

        Targets or end points should never be identified here.
        """
        return (
            self.secure_protocol,
            self.host,
            self.apikey,
            self.region_name,
        )

    def url(self, privacy=False, *args, **kwargs):
        """Returns the URL built dynamically based on specified arguments."""

        # Define any URL parameters
        params = {
            "region": self.region_name,
            "batch": "yes" if self.batch else "no",
        }

        # Append our headers into our parameters
        params.update({f"+{k}": v for k, v in self.headers.items()})

        # Append our template tokens into our parameters
        params.update({f":{k}": v for k, v in self.tokens.items()})

        # Extend our parameters
        params.update(self.url_parameters(privacy=privacy, *args, **kwargs))

        if self.from_addr[0]:
            # from_addr specified; pass it back on the url
            params["name"] = self.from_addr[0]

        if self.cc:
            # Handle our Carbon Copy Addresses
            params["cc"] = ",".join([
                "{}{}".format(
                    "" if not e not in self.names else f"{self.names[e]}:",
                    e,
                )
                for e in self.cc
            ])

        if self.bcc:
            # Handle our Blind Carbon Copy Addresses
            params["bcc"] = ",".join(self.bcc)

        # a simple boolean check as to whether we display our target emails
        # or not
        has_targets = not (
            len(self.targets) == 1 and self.targets[0][1] == self.from_addr
        )

        return "{schema}://{user}@{host}/{apikey}/{targets}/?{params}".format(
            schema=self.secure_protocol,
            host=self.host,
            user=NotifyMailgun.quote(self.user, safe=""),
            apikey=self.pprint(self.apikey, privacy, safe=""),
            targets=(
                ""
                if not has_targets
                else "/".join([
                    NotifyMailgun.quote(
                        "{}{}".format("" if not e[0] else f"{e[0]}:", e[1]),
                        safe="",
                    )
                    for e in self.targets
                ])
            ),
            params=NotifyMailgun.urlencode(params),
        )

    def __len__(self):
        """Returns the number of targets associated with this notification."""
        #
        # Factor batch into calculation
        #
        batch_size = 1 if not self.batch else self.default_batch_size
        targets = len(self.targets)
        if batch_size > 1:
            targets = int(targets / batch_size) + (
                1 if targets % batch_size else 0
            )
        return targets if targets > 0 else 1

    @staticmethod
    def parse_url(url):
        """Parses the URL and returns enough arguments that can allow us to re-
        instantiate this object."""
        results = NotifyBase.parse_url(url, verify_host=False)
        if not results:
            # We're done early as we couldn't load the results
            return results

        # Get our entries; split_path() looks after unquoting content for us
        # by default
        results["targets"] = NotifyMailgun.split_path(results["fullpath"])

        # Our very first entry is reserved for our api key
        try:
            results["apikey"] = results["targets"].pop(0)

        except IndexError:
            # We're done - no API Key found
            results["apikey"] = None

        # Attempt to detect 'from' email address
        if "from" in results["qsd"] and len(results["qsd"]["from"]):
            results["from_addr"] = NotifyMailgun.unquote(
                results["qsd"]["from"]
            )

            if "name" in results["qsd"] and len(results["qsd"]["name"]):
                # Depricate use of both `from=` and `name=` in the same url as
                # they will be synomomus of one another in the future.
                results["from_addr"] = formataddr(
                    (
                        NotifyMailgun.unquote(results["qsd"]["name"]),
                        results["from_addr"],
                    ),
                    charset="utf-8",
                )
                logger.warning(
                    "Mailgun name= and from= are synonymous; "
                    "use one or the other."
                )

        elif "name" in results["qsd"] and len(results["qsd"]["name"]):
            # Extract from name to associate with from address
            results["from_addr"] = NotifyMailgun.unquote(
                results["qsd"]["name"]
            )

        if "region" in results["qsd"] and len(results["qsd"]["region"]):
            # Acquire region if defined
            results["region_name"] = NotifyMailgun.unquote(
                results["qsd"]["region"]
            )

        # Handle 'to' email address
        if "to" in results["qsd"] and len(results["qsd"]["to"]):
            results["targets"].append(results["qsd"]["to"])

        # Handle Carbon Copy Addresses
        if "cc" in results["qsd"] and len(results["qsd"]["cc"]):
            results["cc"] = results["qsd"]["cc"]

        # Handle Blind Carbon Copy Addresses
        if "bcc" in results["qsd"] and len(results["qsd"]["bcc"]):
            results["bcc"] = results["qsd"]["bcc"]

        # Add our Meta Headers that the user can provide with their outbound
        # emails
        results["headers"] = {
            NotifyBase.unquote(x): NotifyBase.unquote(y)
            for x, y in results["qsd+"].items()
        }

        # Add our template tokens (if defined)
        results["tokens"] = {
            NotifyBase.unquote(x): NotifyBase.unquote(y)
            for x, y in results["qsd:"].items()
        }

        # Get Batch Mode Flag
        results["batch"] = parse_bool(
            results["qsd"].get(
                "batch", NotifyMailgun.template_args["batch"]["default"]
            )
        )

        return results
