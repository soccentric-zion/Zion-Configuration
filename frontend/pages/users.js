// Users & Groups page: manage local accounts, roles, groups, and membership.

import { el } from '../core/dom.js';
import { Card } from '../components/card.js';
import { Field } from '../components/field.js';
import { Button } from '../components/button.js';
import { Table } from '../components/table.js';
import { EmptyState } from '../components/empty-state.js';
import { confirmDialog, formDialog, promptDialog } from '../components/modal.js';
import { ActionList, ActionRow } from '../components/action-row.js';

export async function render(api, store) {
    const [users, groups] = await Promise.all([
        api.get('/api/v1/system/users'),
        api.get('/api/v1/system/groups'),
    ]);

    const refresh = () => store.reload();
    const userRows = (users || []).map((user) => {
        const role = Field({
            type: 'select',
            value: user.role,
            help: 'Controls which pages and administrative actions this account can use.',
            options: ['admin', 'operator', 'viewer'].map(value => ({ value, label: value })),
        });
        role.input.style.width = 'auto';
        role.input.addEventListener('change', async () => {
            try {
                await api.put(`/api/v1/system/users/${encodeURIComponent(user.username)}/role`, { role: role.get() });
                store.toast(`${user.username} is now ${role.get()}`, 'success');
            } catch (error) {
                role.set(user.role);
                store.toast(error.message, 'error');
            }
        });
        return [
            user.username,
            role.input,
            (user.groups || []).length ? user.groups.join(', ') : el('span', { class: 'muted' }, 'No groups'),
            el('div', { class: 'table-actions' },
                Button({
                    variant: 'link', size: 'sm', title: `Reset the password for ${user.username}`,
                    onClick: async () => {
                        const password = await promptDialog({ title: `Reset password for ${user.username}`, label: 'New password (min 8)', type: 'password', confirmLabel: 'Set password' });
                        if (password == null) return;
                        if (password.length < 8) { store.toast('Password must be at least 8 characters', 'error'); return; }
                        await api.put(`/api/v1/system/users/${encodeURIComponent(user.username)}/password`, { password });
                        store.toast('Password updated', 'success');
                    },
                }, 'Reset password'),
                Button({
                    variant: 'link', size: 'sm', title: `Delete ${user.username} and remove group memberships`,
                    onClick: async () => {
                        if (!await confirmDialog({ title: `Delete user ${user.username}?`, description: 'This also removes the user from every group.', variant: 'destructive' })) return;
                        await api.del(`/api/v1/system/users/${encodeURIComponent(user.username)}`);
                        refresh();
                    },
                }, 'Delete')),
        ];
    });

    const usersCard = Card({
        title: 'Users',
        subtitle: 'Local accounts and their authorization roles.',
        footer: ActionList(ActionRow('Add user', 'Create a local account with a role and password.', Button({
            onClick: async () => {
                const result = await formDialog({
                    title: 'Add user', confirmLabel: 'Create',
                    fields: [
                        { key: 'username', label: 'Username' },
                        { key: 'password', label: 'Password (min 8)', type: 'password' },
                        { key: 'role', label: 'Role: admin / operator / viewer', placeholder: 'viewer' },
                    ],
                });
                if (!result || !result.username || !result.password) return;
                if (result.password.length < 8) { store.toast('Password must be at least 8 characters', 'error'); return; }
                await api.post('/api/v1/system/users', { username: result.username, password: result.password, role: result.role || 'viewer' });
                refresh();
            },
        }, 'Add user'))),
    }, userRows.length
        ? Table({ columns: ['User', 'Role', 'Groups', ''], caption: 'Users' }, userRows).element
        : EmptyState({ title: 'No users configured' }));

    const rootPassword = Field({ label: 'New root password (min 8)', type: 'password' });
    const rootCard = Card({ title: 'Root password', subtitle: 'Change the Linux root password used for local administration.' }, rootPassword.wrap,
        ActionList(ActionRow('Set root password', 'Replace the current root password with the new value.', Button({
            onClick: async () => {
                if (rootPassword.get().length < 8) { store.toast('Password must be at least 8 characters', 'error'); return; }
                await api.post('/api/v1/system/root-password', { password: rootPassword.get() });
                store.toast('Root password set', 'success');
                rootPassword.set('');
            },
        }, 'Set password'))));

    const groupCards = (groups || []).map((group) => {
        const membersList = group.members || [];
        const available = (users || []).filter(user => !membersList.includes(user.username));
        const memberPicker = Field({
            label: 'Add user to group',
            type: 'select',
            value: available[0]?.username || '',
            options: available.length
                ? available.map(user => ({ value: user.username, label: user.username }))
                : [{ value: '', label: 'All users assigned' }],
        });
        const members = membersList.length
            ? el('div', { class: 'stack' },
                el('div', { class: 'field-help' }, 'Accounts currently assigned to this group.'),
                ...membersList.map(username => el('div', { class: 'row' },
                    el('span', {}, username),
                    Button({
                        title: `Remove ${username} from ${group.name}`,
                        variant: 'link', size: 'sm',
                        onClick: async () => {
                            await api.del(`/api/v1/system/groups/${encodeURIComponent(group.name)}/members/${encodeURIComponent(username)}`);
                            refresh();
                        },
                    }, 'Remove'))))
            : el('p', { class: 'muted' }, 'No members assigned. Add an account below to grant this group membership.');
        return Card({
            title: group.name,
            subtitle: 'Group membership and assignment controls.',
            footer: ActionList(
                ActionRow('Assign user', 'Add a selected account to this group.', Button({
                    onClick: async () => {
                        if (!memberPicker.get()) return;
                        await api.put(`/api/v1/system/groups/${encodeURIComponent(group.name)}/members/${encodeURIComponent(memberPicker.get())}`, {});
                        refresh();
                    },
                }, 'Assign user')),
                ActionRow('Delete group', 'Remove this group and clear its membership assignments.', Button({
                    variant: 'outline',
                    onClick: async () => {
                        if (!await confirmDialog({ title: `Delete group ${group.name}?`, description: 'Users will remain, but their membership in this group will be removed.', variant: 'destructive' })) return;
                        await api.del(`/api/v1/system/groups/${encodeURIComponent(group.name)}`);
                        refresh();
                    },
                }, 'Delete group'))),
        }, members, el('div', { class: 'field-row' }, memberPicker.wrap));
    });

    const groupsHeader = Card({
        title: 'Groups',
        subtitle: 'Organize users into named groups and assign membership.',
        footer: ActionList(ActionRow('Add group', 'Create a named group that can contain local users.', Button({
            onClick: async () => {
                const result = await formDialog({ title: 'Add group', confirmLabel: 'Create', fields: [{ key: 'name', label: 'Group name' }] });
                if (!result || !result.name) return;
                await api.post('/api/v1/system/groups', { name: result.name });
                refresh();
            },
        }, 'Add group'))),
    });

    return el('div', {},
        el('div', { class: 'page-header' }, el('h1', {}, 'Users & Groups'), el('p', {}, 'Manage local accounts, roles, groups, and group membership.')),
        usersCard,
        rootCard,
        groupsHeader,
        groupCards.length
            ? el('div', { class: 'card-grid' }, ...groupCards)
            : EmptyState({ title: 'No groups configured' }));
}
