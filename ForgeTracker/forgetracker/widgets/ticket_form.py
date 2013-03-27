from allura.lib.widgets import form_fields as ffw

from pylons import tmpl_context as c
from formencode import validators as fev

import ew as ew_core
import ew.jinja2_ew as ew

from allura import model as M

class TicketCustomFields(ew.CompoundField):
    template='jinja:forgetracker:templates/tracker_widgets/ticket_custom_fields.html'

    def __init__(self, *args, **kwargs):
        super(TicketCustomFields, self).__init__(*args, **kwargs)
        self._fields = None

    @property
    def fields(self):
        # milestone is kind of special because of the layout
        # add it to the main form rather than handle with the other customs
        if self._fields is None:
            self._fields = []
            for cf in c.app.globals.custom_fields:
                if cf.name != '_milestone':
                    self._fields.append(TicketCustomField.make(cf))
        return self._fields

class GenericTicketForm(ew.SimpleForm):
    defaults=dict(
        ew.SimpleForm.defaults,
        name="ticket_form",
        submit_text='Save',
        ticket=None,
        show_comment=False)

    def display_field_by_name(self, idx, ignore_errors=False):
        field = self.fields[idx]
        ctx = self.context_for(field)
        if idx == 'assigned_to':
            self._add_current_value_to_user_field(field, ctx.get('value'))
        elif idx == 'custom_fields':
            for cf in c.app.globals.custom_fields:
                if cf and cf.type == 'user':
                    val = ctx.get('value')
                    user = val.get(cf.name) if val else None
                    for f in field.fields:
                        if f.name == cf.name:
                            self._add_current_value_to_user_field(f, user)

        display = field.display(**ctx)
        if ctx['errors'] and field.show_errors and not ignore_errors:
            display = "%s<div class='error'>%s</div>" % (display, ctx['errors'])
        return display

    def _add_current_value_to_user_field(self, field, user):
        """Adds current field's value to `ProjectUserCombo` options.

        This is done to be able to select default value when widget loads,
        since normally `ProjectUserCombo` shows without any options, and loads
        them asynchronously (via ajax).
        """
        if isinstance(user, basestring):
            user = M.User.by_username(user)
        if user and user != M.User.anonymous():
            field.options = [
                ew.Option(
                    py_value=user.username,
                    label='%s (%s)' % (user.display_name, user.username))
            ]

    @property
    def fields(self):
        fields = [
            ew.TextField(name='summary', label='Title',
                attrs={'style':'width: 425px','placeholder':'Title'},
                validator=fev.UnicodeString(not_empty=True, messages={'empty':"You must provide a Title"})),
            ffw.MarkdownEdit(label='Description',name='description',
                    attrs={'style':'width: 95%'}),
            ew.SingleSelectField(name='status', label='Status',
                options=lambda: c.app.globals.all_status_names.split()),
            ffw.ProjectUserCombo(name='assigned_to', label='Owner'),
            ffw.LabelEdit(label='Labels',name='labels', className='ticket_form_tags'),
            ew.Checkbox(name='private', label='Mark as Private', attrs={'class':'unlabeled'}),
            ew.InputField(name='attachment', label='Attachment', field_type='file', validator=fev.FieldStorageUploadConverter(if_missing=None)),
            ffw.MarkdownEdit(name='comment', label='Comment',
                        attrs={'style':'min-height:7em; width:97%'}),
            ew.SubmitButton(label=self.submit_text,name='submit',
                attrs={'class':"ui-button ui-widget ui-state-default ui-button-text-only"}),
            ew.HiddenField(name='ticket_num', validator=fev.Int(if_missing=None)),
        ]
        # milestone is kind of special because of the layout
        # add it to the main form rather than handle with the other customs
        if c.app.globals.custom_fields:
            for cf in c.app.globals.custom_fields:
                if cf.name == '_milestone':
                    fields.append(TicketCustomField.make(cf))
                    break
        return ew_core.NameList(fields)

class TicketForm(GenericTicketForm):
    template='jinja:forgetracker:templates/tracker_widgets/ticket_form.html'
    @property
    def fields(self):
        fields = ew_core.NameList(super(TicketForm, self).fields)
        if c.app.globals.custom_fields:
            fields.append(TicketCustomFields(name="custom_fields"))
        return fields

    def resources(self):
        for r in super(TicketForm, self).resources(): yield r
        yield ew.JSScript('''
        $(function(){
            $('#show_attach input').click(function(){
                $('#view_attach').show();
                $('#show_attach').hide();
            });
            $('form').submit(function() {
                $('input[type=submit]', this).attr('disabled', 'disabled');
            });
            $('div.reply.discussion-post a.markdown_preview').click(function(){
                var arrow = $(this).closest('.discussion-post').find('span.arw');
                arrow.hide();
            });
            $('div.reply.discussion-post a.markdown_edit').click(function(){
                var arrow = $(this).closest('.discussion-post').find('span.arw');
                arrow.show();
            });
        });''')

class TicketCustomField(object):

    def _select(field):
        options = []
        for opt in field.options.split():
            selected = False
            if opt.startswith('*'):
                opt = opt[1:]
                selected = True
            options.append(ew.Option(label=opt,html_value=opt,py_value=opt,selected=selected))
        return ew.SingleSelectField(label=field.label, name=str(field.name), options=options)

    def _milestone(field):
        options = []
        for m in field.milestones:
            options.append(ew.Option(
                label=m.name,
                py_value=m.name,
                complete=bool(m.complete)))

        ssf = MilestoneField(
            label=field.label, name=str(field.name),
            options=options)
        return ssf

    def _boolean(field):
        return ew.Checkbox(label=field.label, name=str(field.name), suppress_label=True)

    def _number(field):
        return ew.NumberField(label=field.label, name=str(field.name))

    def _user(field):
        return ffw.ProjectUserCombo(label=field.label, name=str(field.name))

    @staticmethod
    def _default(field):
        return ew.TextField(label=field.label, name=str(field.name))

    SELECTOR = dict(
        select=_select,
        milestone=_milestone,
        boolean=_boolean,
        number=_number,
        user=_user)

    @classmethod
    def make(cls, field):
        factory = cls.SELECTOR.get(field.get('type'), cls._default)
        return factory(field)

class MilestoneField(ew.SingleSelectField):
    def display(self, *args, **kwargs):
        milestone_value = kwargs['value']
        for milestone in reversed(self.options):  # reverse so del hits the correct indexes
            if milestone.complete and (milestone.py_value != milestone_value):
                del self.options[self.options.index(milestone)]
        return super(MilestoneField, self).display(*args, **kwargs)
