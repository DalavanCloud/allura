# -*- coding: utf-8 -*-

#       Licensed to the Apache Software Foundation (ASF) under one
#       or more contributor license agreements.  See the NOTICE file
#       distributed with this work for additional information
#       regarding copyright ownership.  The ASF licenses this file
#       to you under the Apache License, Version 2.0 (the
#       "License"); you may not use this file except in compliance
#       with the License.  You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#       Unless required by applicable law or agreed to in writing,
#       software distributed under the License is distributed on an
#       "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#       KIND, either express or implied.  See the License for the
#       specific language governing permissions and limitations
#       under the License.

import re
import logging
from urlparse import urljoin

from tg import config
from bs4 import BeautifulSoup
import html5lib
import html5lib.serializer
import html5lib.filters.alphabeticalattributes
import markdown
import emoji

from . import macro
from . import helpers as h
from allura import model as M
from allura.lib.utils import ForgeHTMLSanitizerFilter, is_nofollow_url

log = logging.getLogger(__name__)

PLAINTEXT_BLOCK_RE = re.compile(
    r'(?P<bplain>\[plain\])(?P<code>.*?)(?P<eplain>\[\/plain\])',
    re.MULTILINE | re.DOTALL
)

MACRO_PATTERN = r'\[\[([^\]\[]+)\]\]'


class CommitMessageExtension(markdown.Extension):

    """Markdown extension for processing commit messages.

    People don't expect their commit messages to be parsed as Markdown. This
    extension is therefore intentionally minimal in what it does. It knows how
    to handle Trac-style short refs, will replace short refs with links, and
    will create paragraphs around double-line breaks. That is *all* it does.

    To make it do more, re-add some inlinePatterns and/or blockprocessors.

    Some examples of the Trac-style refs this extension can parse::

        #100
        r123
        ticket:100
        comment:13:ticket:100
        source:path/to/file.c@123#L456 (rev 123, lineno 456)

    Trac-style refs will be converted to links to the appropriate artifact by
    the :class:`PatternReplacingProcessor` preprocessor.

    """

    def __init__(self, app):
        markdown.Extension.__init__(self)
        self.app = app
        self._use_wiki = False

    def extendMarkdown(self, md, md_globals):
        md.registerExtension(self)
        # remove default preprocessors and add our own
        md.preprocessors.clear()
        md.preprocessors['trac_refs'] = PatternReplacingProcessor(TracRef1(), TracRef2(), TracRef3(self.app))
        # remove all inlinepattern processors except short refs and links
        md.inlinePatterns.clear()
        md.inlinePatterns["link"] = markdown.inlinepatterns.LinkPattern(markdown.inlinepatterns.LINK_RE, md)
        md.inlinePatterns['short_reference'] = ForgeLinkPattern(markdown.inlinepatterns.SHORT_REF_RE, md, ext=self)
        # remove all default block processors except for paragraph
        md.parser.blockprocessors.clear()
        md.parser.blockprocessors['paragraph'] = markdown.blockprocessors.ParagraphProcessor(md.parser)
        # wrap artifact link text in square brackets
        self.forge_link_tree_processor = ForgeLinkTreeProcessor(md)
        md.treeprocessors['links'] = self.forge_link_tree_processor
        # Sanitize HTML
        md.postprocessors['sanitize_html'] = HTMLSanitizer()
        # Put a class around markdown content for custom css
        md.postprocessors['add_custom_class'] = AddCustomClass()
        md.postprocessors['mark_safe'] = MarkAsSafe()

    def reset(self):
        self.forge_link_tree_processor.reset()


class Pattern(object):

    """Base class for regex patterns used by the :class:`PatternReplacingProcessor`.

    Subclasses must define :attr:`pattern` (a compiled regex), and
    :meth:`repl`.

    """
    BEGIN, END = r'(^|\b|\s)', r'($|\b|\s)'

    def sub(self, line):
        return self.pattern.sub(self.repl, line)

    def repl(self, match):
        """Return a string to replace ``match`` in the source string (the
        string in which the match was found).

        """
        return match.group()


class TracRef1(Pattern):

    """Replaces Trac-style short refs with links. Example patterns::

        #100 (ticket 100)
        r123 (revision 123)

    """
    pattern = re.compile(r'(?<!\[|\w)([#r]\d+)(?!\]|\w)')

    def repl(self, match):
        shortlink = M.Shortlink.lookup(match.group(1))
        if shortlink and not getattr(shortlink.ref.artifact, 'deleted', False):
            return '[{ref}]({url})'.format(
                ref=match.group(1),
                url=shortlink.url)
        return match.group()


class TracRef2(Pattern):

    """Replaces Trac-style short refs with links. Example patterns::

        ticket:100
        comment:13:ticket:400

    """
    pattern = re.compile(
        Pattern.BEGIN + r'((comment:(\d+):)?(ticket:)(\d+))' + Pattern.END)

    def repl(self, match):
        shortlink = M.Shortlink.lookup('#' + match.group(6))
        if shortlink and not getattr(shortlink.ref.artifact, 'deleted', False):
            url = shortlink.url
            if match.group(4):
                slug = self.get_comment_slug(
                    shortlink.ref.artifact, match.group(4))
                slug = '#' + slug if slug else ''
                url = url + slug

            return '{front}[{ref}]({url}){back}'.format(
                front=match.group(1),
                ref=match.group(2),
                url=url,
                back=match.group(7))
        return match.group()

    def get_comment_slug(self, ticket, comment_num):
        """Given the id of an imported Trac comment, return it's Allura slug.

        """
        if not ticket:
            return None

        comment_num = int(comment_num)
        comments = ticket.discussion_thread.post_class().query.find(dict(
            discussion_id=ticket.discussion_thread.discussion_id,
            thread_id=ticket.discussion_thread._id,
            status={'$in': ['ok', 'pending']},
            deleted=False)).sort('timestamp')

        if comment_num <= comments.count():
            return comments.all()[comment_num - 1].slug


class TracRef3(Pattern):

    """Replaces Trac-style short refs with links. Example patterns::

        source:trunk/server/file.c@123#L456 (rev 123, lineno 456)

    Creates a link to a specific line of a source file at a specific revision.

    """
    pattern = re.compile(
        Pattern.BEGIN + r'((source:)([^@#\s]+)(@(\w+))?(#L(\d+))?)' + Pattern.END)

    def __init__(self, app):
        super(Pattern, self).__init__()
        self.app = app

    def repl(self, match):
        if not self.app:
            return match.group()
        file, rev, lineno = (
            match.group(4),
            match.group(6) or 'HEAD',
            '#l' + match.group(8) if match.group(8) else '')
        url = '{app_url}{rev}/tree/{file}{lineno}'.format(
            app_url=self.app.url,
            rev=rev,
            file=file,
            lineno=lineno)
        return '{front}[{ref}]({url}){back}'.format(
            front=match.group(1),
            ref=match.group(2),
            url=url,
            back=match.group(9))


class PatternReplacingProcessor(markdown.preprocessors.Preprocessor):

    """A Markdown preprocessor that searches the source lines for patterns and
    replaces matches with alternate text.

    """

    def __init__(self, *patterns):
        self.patterns = patterns or []

    def run(self, lines):
        new_lines = []
        for line in lines:
            for pattern in self.patterns:
                line = pattern.sub(line)
            new_lines.append(line)
        return new_lines


class ForgeExtension(markdown.Extension):

    def __init__(self, wiki=False, email=False, macro_context=None):
        markdown.Extension.__init__(self)
        self._use_wiki = wiki
        self._is_email = email
        self._macro_context = macro_context

    def extendMarkdown(self, md, md_globals):
        md.registerExtension(self)
        # allow markdown within e.g. <div markdown>...</div>  More info at:
        # https://github.com/waylan/Python-Markdown/issues/52
        md.preprocessors['html_block'].markdown_in_raw = True
        md.preprocessors.add('plain_text_block', PlainTextPreprocessor(md), "_begin")
        md.preprocessors.add('macro_include', ForgeMacroIncludePreprocessor(md), '_end')
        # this has to be before the 'escape' processor, otherwise weird
        # placeholders are inserted for escaped chars within urls, and then the
        # autolink can't match the whole url
        md.inlinePatterns.add('autolink_without_brackets', AutolinkPattern(r'(http(?:s?)://[a-zA-Z0-9./\-\\_%?&=+#;~:!]+)', md), '<escape')
        # replace the link pattern with our extended version
        md.inlinePatterns['link'] = ForgeLinkPattern(markdown.inlinepatterns.LINK_RE, md, ext=self)
        md.inlinePatterns['short_reference'] = ForgeLinkPattern(markdown.inlinepatterns.SHORT_REF_RE, md, ext=self)
        # macro must be processed before links
        md.inlinePatterns.add('macro', ForgeMacroPattern(MACRO_PATTERN, md, ext=self), '<link')
        self.forge_link_tree_processor = ForgeLinkTreeProcessor(md)
        md.treeprocessors['links'] = self.forge_link_tree_processor
        # Sanitize HTML
        md.postprocessors['sanitize_html'] = HTMLSanitizer()
        # Rewrite all relative links that don't start with . to have a '../' prefix
        md.postprocessors['rewrite_relative_links'] = RelativeLinkRewriter(make_absolute=self._is_email)
        # Put a class around markdown content for custom css
        md.postprocessors['add_custom_class'] = AddCustomClass()
        md.postprocessors['mark_safe'] = MarkAsSafe()

    def reset(self):
        self.forge_link_tree_processor.reset()


class EmojiExtension(markdown.Extension):

    EMOJI_RE = u'(%s[a-zA-Z0-9\+\-_&.ô’Åéãíç()!#*]+%s)' % (':', ':')

    def __init__(self, **kwargs):
        markdown.Extension.__init__(self)
        
    def extendMarkdown(self, md, md_globals):
        md.inlinePatterns["emoji"] = EmojiInlinePattern(self.EMOJI_RE)

        
class EmojiInlinePattern(markdown.inlinepatterns.Pattern):
    
    def __init__(self, pattern):
        markdown.inlinepatterns.Pattern.__init__(self, pattern)
        
    def handleMatch(self, m):
        emoji_code = m.group(2)
        return emoji.emojize(emoji_code, use_aliases=True)


class ForgeLinkPattern(markdown.inlinepatterns.LinkPattern):

    artifact_re = re.compile(r'((.*?):)?((.*?):)?(.+)')

    def __init__(self, *args, **kwargs):
        self.ext = kwargs.pop('ext')
        markdown.inlinepatterns.LinkPattern.__init__(self, *args, **kwargs)

    def handleMatch(self, m):
        el = markdown.util.etree.Element('a')
        el.text = m.group(2)
        is_link_with_brackets = False
        try:
            href = m.group(9)
        except IndexError:
            href = m.group(2)
            is_link_with_brackets = True
            if el.text == 'x' or el.text == ' ': # skip [ ] and [x] for markdown checklist 
                return '[' + el.text + ']'
        try:
            title = m.group(13)
        except IndexError:
            title = None

        classes = ''
        if href:
            if href == 'TOC':
                return '[TOC]'  # skip TOC
            if self.artifact_re.match(href):
                href, classes = self._expand_alink(href, is_link_with_brackets)
            el.set('href', self.sanitize_url(self.unescape(href.strip())))
            el.set('class', classes)
        else:
            el.set('href', '')

        if title:
            title = markdown.inlinepatterns.dequote(self.unescape(title))
            el.set('title', title)

        if 'notfound' in classes and not self.ext._use_wiki:
            text = el.text
            el = markdown.util.etree.Element('span')
            el.text = '[%s]' % text
        return el

    def _expand_alink(self, link, is_link_with_brackets):
        '''Return (href, classes) for an artifact link'''
        classes = ''
        if is_link_with_brackets:
            classes = 'alink'
        href = link
        shortlink = M.Shortlink.lookup(link)
        if shortlink and shortlink.ref and not getattr(shortlink.ref.artifact, 'deleted', False):
            href = shortlink.url
            if getattr(shortlink.ref.artifact, 'is_closed', False):
                classes += ' strikethrough'
            self.ext.forge_link_tree_processor.alinks.append(shortlink)
        elif is_link_with_brackets:
            href = h.urlquote(link)
            classes += ' notfound'
        attach_link = link.split('/attachment/')
        if len(attach_link) == 2 and self.ext._use_wiki:
            shortlink = M.Shortlink.lookup(attach_link[0])
            if shortlink:
                attach_status = ' notfound'
                for attach in shortlink.ref.artifact.attachments:
                    if attach.filename == attach_link[1]:
                        attach_status = ''
                classes += attach_status
        return href, classes


class PlainTextPreprocessor(markdown.preprocessors.Preprocessor):

    '''
    This was used earlier for [plain] tags that the Blog tool's rss importer
    created, before html2text did good escaping of all special markdown chars.
    Can be deprecated.
    '''

    def run(self, lines):
        text = "\n".join(lines)
        while 1:
            res = PLAINTEXT_BLOCK_RE.finditer(text)
            for m in res:
                code = self._escape(m.group('code'))
                placeholder = self.markdown.htmlStash.store(code, safe=True)
                text = '%s%s%s' % (
                    text[:m.start()], placeholder, text[m.end():])
                break
            else:
                break
        return text.split("\n")

    def _escape(self, txt):
        """ basic html escaping """
        txt = txt.replace('&', '&amp;')
        txt = txt.replace('<', '&lt;')
        txt = txt.replace('>', '&gt;')
        txt = txt.replace('"', '&quot;')
        return txt


class ForgeMacroPattern(markdown.inlinepatterns.Pattern):

    def __init__(self, *args, **kwargs):
        self.ext = kwargs.pop('ext')
        self.macro = macro.parse(self.ext._macro_context)
        markdown.inlinepatterns.Pattern.__init__(self, *args, **kwargs)

    def handleMatch(self, m):
        html = self.macro(m.group(2))
        placeholder = self.markdown.htmlStash.store(html)
        return placeholder


class ForgeLinkTreeProcessor(markdown.treeprocessors.Treeprocessor):

    '''Wraps artifact links with []'''

    def __init__(self, parent):
        self.parent = parent
        self.alinks = []

    def run(self, root):
        for node in root.getiterator('a'):
            if 'alink' in node.get('class', '').split() and node.text:
                node.text = '[' + node.text + ']'
        return root

    def reset(self):
        self.alinks = []


class MarkAsSafe(markdown.postprocessors.Postprocessor):

    def run(self, text):
        return h.html.literal(text)


class AddCustomClass(markdown.postprocessors.Postprocessor):

    def run(self, text):
        return '<div class="markdown_content">%s</div>' % text


class RelativeLinkRewriter(markdown.postprocessors.Postprocessor):

    def __init__(self, make_absolute=False):
        self._make_absolute = make_absolute

    def run(self, text):
        soup = BeautifulSoup(text, 'html5lib')  # 'html.parser' parser gives weird </li> behaviour with test_macro_members

        if self._make_absolute:
            rewrite = self._rewrite_abs
        else:
            rewrite = self._rewrite
        for link in soup.findAll('a'):
            rewrite(link, 'href')
        for link in soup.findAll('img'):
            rewrite(link, 'src')

        # html5lib parser adds html/head/body tags, so output <body> without its own tags
        return unicode(soup.body)[len('<body>'):-len('</body>')]

    def _rewrite(self, tag, attr):
        val = tag.get(attr)
        if val is None:
            return
        if ' ' in val:
            # Don't urllib.quote to avoid possible double-quoting
            # just make sure no spaces
            val = val.replace(' ', '%20')
            tag[attr] = val
        if '://' in val:
            if is_nofollow_url(val):
                tag['rel'] = 'nofollow'
            return
        if val.startswith('/'):
            return
        if val.startswith('.'):
            return
        if val.startswith('mailto:'):
            return
        if val.startswith('#'):
            return
        tag[attr] = '../' + val

    def _rewrite_abs(self, tag, attr):
        self._rewrite(tag, attr)
        val = tag.get(attr)
        val = urljoin(config['base_url'], val)
        tag[attr] = val


class HTMLSanitizer(markdown.postprocessors.Postprocessor):

    def run(self, text):
        parsed = html5lib.parseFragment(text)

        # if we didn't have to customize our sanitization, could just do:
        # return html5lib.serialize(parsed, sanitize=True)

        # instead we do the same steps as that function,
        # but add our ForgeHTMLSanitizerFilter instead of sanitize=True which would use the standard one
        TreeWalker = html5lib.treewalkers.getTreeWalker("etree")
        walker = TreeWalker(parsed)
        walker = ForgeHTMLSanitizerFilter(walker)  # this is our custom step
        s = html5lib.serializer.HTMLSerializer()
        return s.render(walker)


class AutolinkPattern(markdown.inlinepatterns.Pattern):

    def __init__(self, pattern, markdown_instance=None):
        markdown.inlinepatterns.Pattern.__init__(
            self, pattern, markdown_instance)
        # override the complete regex, requiring the preceding text (.*?) to end
        # with whitespace or beginning of line "\s|^"
        self.compiled_re = re.compile("^(.*?\s|^)%s(.*?)$" % pattern,
                                      re.DOTALL | re.UNICODE)

    def handleMatch(self, mo):
        old_link = mo.group(2)
        result = markdown.util.etree.Element('a')
        result.text = old_link
        # since this is run before the builtin 'escape' processor, we have to
        # do our own unescaping
        for char in markdown.Markdown.ESCAPED_CHARS:
            old_link = old_link.replace('\\' + char, char)
        result.set('href', old_link)
        return result


class ForgeMacroIncludePreprocessor(markdown.preprocessors.Preprocessor):

    '''Join include statements to prevent extra <br>'s inserted by nl2br extension.

    Converts:
    [[include ref=some_ref]]
    [[include ref=some_other_ref]]

    To:
    [[include ref=some_ref]][[include ref=some_other_ref]]
    '''
    pattern = re.compile(r'^\s*\[\[include ref=[^\]]*\]\]\s*$', re.IGNORECASE)

    def run(self, lines):
        buf = []
        result = []
        for line in lines:
            if self.pattern.match(line):
                buf.append(line)
            else:
                if buf:
                    result.append(''.join(buf))
                    buf = []
                result.append(line)
        return result
