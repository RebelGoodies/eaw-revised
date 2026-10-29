#!/usr/bin/env bash

set -u

discover_player() {
    local script_dir=$1
    local -a candidates=()
    if [[ -n ${EAWR_PLAYER:-} ]]; then
        player=$EAWR_PLAYER
    elif [[ -x $script_dir/AppRun ]]; then
        player=$script_dir/AppRun
    elif [[ -x $script_dir/eawr-launcher ]]; then
        player=$script_dir/eawr-launcher
    else
        shopt -s nullglob
        candidates+=("$script_dir"/*-player*.AppImage)
        if ((${#candidates[@]} == 0)); then
            candidates+=("$script_dir"/out/release/*-player*.AppImage)
        fi
        if ((${#candidates[@]} == 0)); then
            candidates+=("$script_dir"/out/local-player/*-player*.AppImage)
        fi
        shopt -u nullglob
        if ((${#candidates[@]} != 1)); then
            printf 'Cannot choose player: found %d matching AppImages. Set EAWR_PLAYER to an executable path.\n' "${#candidates[@]}" >&2
            return 1
        fi
        player=${candidates[0]}
    fi
    if [[ ! -x $player ]]; then
        printf 'Player is missing or not executable: %s\n' "$player" >&2
        return 1
    fi
}

discover_workshop_dirs() {
    local root vdf line library
    local -a steam_roots=()
    local -A seen_libraries=()
    if [[ -n ${EAWR_WORKSHOP_DIR:-} ]]; then
        workshop_dirs=("$EAWR_WORKSHOP_DIR")
    fi
    steam_roots=(
        "$HOME/.local/share/Steam" "$HOME/.steam/steam" "$HOME/.steam/root"
        "${XDG_DATA_HOME:-$HOME/.local/share}/Steam"
        "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam"
        "$HOME/.var/app/com.valvesoftware.Steam/data/Steam"
    )
    for root in "${steam_roots[@]}"; do
        [[ -d $root ]] || continue
        root=$(cd -- "$root" && pwd -P) || continue
        if [[ ! ${seen_libraries[$root]+yes} ]]; then
            seen_libraries[$root]=1
            steam_libraries+=("$root")
        fi
        vdf=$root/steamapps/libraryfolders.vdf
        [[ -r $vdf ]] || continue
        while IFS= read -r line || [[ -n $line ]]; do
            [[ $line =~ ^[[:space:]]*\"path\"[[:space:]]+\"([^\"]+)\" ]] || continue
            library=${BASH_REMATCH[1]//\\\\/\\}
            [[ -d $library ]] || continue
            library=$(cd -- "$library" && pwd -P) || continue
            if [[ ! ${seen_libraries[$library]+yes} ]]; then
                seen_libraries[$library]=1
                steam_libraries+=("$library")
            fi
        done < "$vdf"
    done
    if [[ -z ${EAWR_WORKSHOP_DIR:-} ]]; then
        for library in "${steam_libraries[@]}"; do
            workshop_dirs+=("$library/steamapps/workshop/content/32470")
        done
    fi
}

discover_local_mod_dirs() {
    if [[ -z ${EAWR_EAW_GAME_ROOT:-} && -n ${EAW_GAME_ROOT:-} ]]; then
        export EAWR_EAW_GAME_ROOT=$EAW_GAME_ROOT
    fi
    local game_root=${EAWR_EAW_GAME_ROOT:-${EAW_GAME_ROOT:-}} library candidate
    while (($#)); do
        case $1 in
            --) break ;;
            --game-root) if (($# >= 2)); then game_root=$2; shift; fi ;;
        esac
        shift
    done
    if [[ -n $game_root ]]; then
        while [[ $game_root == */ && $game_root != / ]]; do game_root=${game_root%/}; done
        local leaf=${game_root##*/}
        if [[ ${leaf,,} == corruption || ${leaf,,} == gamedata ]] && [[ $game_root == */* ]]; then
            game_root=${game_root%/*}
            game_root=${game_root:-/}
        fi
        local_mod_dirs+=("$game_root/Mods" "$game_root/corruption/Mods")
        return
    fi
    # Match launcher discovery's common-folder fallback, including installs without a manifest.
    for library in "${steam_libraries[@]}"; do
        for candidate in "$library/steamapps/common"/*; do
            [[ -d $candidate/GameData && -d $candidate/corruption ]] || continue
            local_mod_dirs+=("$candidate/Mods" "$candidate/corruption/Mods")
        done
    done
}

load_mod_title() {
    local info=$1 line
    local name_pattern='"name"[[:space:]]*:[[:space:]]*"([^"\\]*)"[[:space:]]*[,}]'
    if [[ -r $info ]]; then
        while IFS= read -r line || [[ -n $line ]]; do
            # shortcut: escaped names use the ID, add JSON decoding if full titles are needed.
            if [[ $line =~ $name_pattern ]]; then
                title=${BASH_REMATCH[1]}
                break
            fi
        done < "$info"
    fi
    title=${title//[$'\001'-$'\037'$'\177']/\?}
}

load_mods() {
    local workshop_dir mod_path mod_id canonical title
    local -A seen_mods=() seen_workshop_ids=()
    local directory_index=0 is_workshop
    for workshop_dir in "${workshop_dirs[@]}" "${local_mod_dirs[@]}"; do
        is_workshop=0
        ((directory_index++ < ${#workshop_dirs[@]})) && is_workshop=1
        [[ -d $workshop_dir ]] || continue
        for mod_path in "$workshop_dir"/*; do
            [[ -d $mod_path/Data ]] || continue
            mod_id=${mod_path##*/}
            if ((is_workshop)); then
                [[ $mod_id =~ ^[0-9]+$ ]] || continue
                [[ ${seen_workshop_ids[$mod_id]+yes} ]] && continue
            fi
            canonical=$(cd -- "$mod_path" && pwd -P) || continue
            [[ ${seen_mods[$canonical]+yes} ]] && continue
            seen_mods[$canonical]=1
            ((is_workshop)) && seen_workshop_ids[$mod_id]=1
            title=''
            load_mod_title "$mod_path/modinfo.json"
            if [[ -n $title ]]; then
                title+=" ($mod_id)"
            else
                title=$mod_id
            fi
            mod_ids+=("$mod_id")
            mod_paths+=("$canonical")
            mod_titles+=("$title")
        done
    done
}

restore_cursor() {
    printf '\033[?25h\n';
}

render_menu() {
    local first last i marker pointer title_width title suffix
    ((old_rows)) && printf '\r\033[%dA\033[J' "$old_rows"
    first=$((cursor - window_size / 2))
    ((first < 0)) && first=0
    ((first + window_size > ${#mod_ids[@]})) && first=$((${#mod_ids[@]} - window_size))
    ((first < 0)) && first=0
    last=$((first + window_size))
    ((last > ${#mod_ids[@]})) && last=${#mod_ids[@]}
    printf '%.*s\n' "$((columns - 1))" 'Optionally select a mod: Number+Enter or arrows+Space+Enter.'
    printf 'Pressing Enter alone launches vanilla. q: quit\n\n'
    printf '%d mods, showing %d-%d\n' "${#mod_ids[@]}" "$((first + 1))" "$last"
    for ((i=first; i<last; i++)); do
        marker=' '
        ((i == marked)) && marker='x'
        pointer=' '
        ((i == cursor)) && pointer='>'
        title=${mod_titles[i]}
        suffix=''
        if [[ $title != "${mod_ids[i]}" ]]; then
            suffix=" (${mod_ids[i]})"
            title=${title%"$suffix"}
        fi
        title_width=$((columns - ${#suffix} - ${#i} - 10))
        ((title_width < 1)) && title_width=1
        title=${title:0:title_width}
        printf '%s [%s] %d) %s%s\n' "$pointer" "$marker" "$((i + 1))" "$title" "$suffix"
    done
    printf 'Selection: %s' "$number_buffer"
    old_rows=$((last - first + 4))
}

select_interactively() {
    local cursor=0 marked=-1 number_buffer='' old_rows=0
    local window_size=$(($1 - 5)) columns=$2 key number
    trap restore_cursor EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP
    printf '\033[?25l'
    while :; do
        render_menu
        IFS= read -rsn1 key || { printf '\nInput closed; not launching.\n' >&2; exit 1; }
        case $key in
            $'\x1b')
                if IFS= read -rsn1 -t 0.1 key && [[ $key == '[' || $key == O ]] && IFS= read -rsn1 -t 0.1 key; then
                    case $key in
                        A) number_buffer=''; ((cursor > 0)) && ((cursor--)) ;;
                        B) number_buffer=''; ((cursor + 1 < ${#mod_ids[@]})) && ((cursor++)) ;;
                    esac
                fi
                ;;
            ' ') if ((marked == cursor)); then marked=-1; else marked=$cursor; fi ;;
            '')
                if [[ -n $number_buffer ]]; then
                    if ((${#number_buffer} <= 9)); then number=$((10#$number_buffer)); else number=0; fi
                    if ((number >= 1 && number <= ${#mod_ids[@]})); then
                        selected=$((number - 1))
                        break
                    fi
                    printf '\nInvalid selection. Enter a number from 1 to %d.\n' "${#mod_ids[@]}"
                    number_buffer=''
                    old_rows=0
                else
                    selected=$marked
                    break
                fi
                ;;
            [0-9]) number_buffer+=$key ;;
            $'\177'|$'\b') number_buffer=${number_buffer%?} ;;
            q|Q) exit 0 ;;
            $'\x03') exit 0 ;;
        esac
    done
    restore_cursor
    trap - EXIT INT TERM HUP
}

select_numbered() {
    local i input='' number
    printf 'Select a mod number, or press Enter for vanilla (q to quit):\n'
    for ((i=0; i<${#mod_ids[@]}; i++)); do
        printf '%d) %s\n' "$((i + 1))" "${mod_titles[i]}"
    done
    while :; do
        IFS= read -r input || { printf 'Input closed; not launching.\n' >&2; exit 1; }
        [[ -z $input ]] && break
        [[ $input == q || $input == Q ]] && exit 0
        if [[ $input =~ ^[0-9]+$ ]] && ((${#input} <= 9)); then number=$((10#$input)); else number=0; fi
        if ((number >= 1 && number <= ${#mod_ids[@]})); then
            selected=$((number - 1))
            break
        fi
        printf 'Invalid selection. Enter a number from 1 to %d, or Enter for vanilla.\n' "${#mod_ids[@]}"
    done
}

select_mod() {
    local interactive=0 rows=24 columns=80
    if [[ -t 0 && -t 1 && ${TERM:-dumb} != dumb ]]; then
        if command -v stty >/dev/null 2>&1; then
            read -r rows columns < <(stty size 2>/dev/null) || { rows=24; columns=80; }
        fi
        ((rows >= 8 && columns >= 40)) && interactive=1
    fi

    if ((${#mod_ids[@]} == 0)); then
        printf 'No installed mods found. Launching vanilla.\n'
    elif ((interactive)); then
        select_interactively "$rows" "$columns"
    else
        select_numbered
    fi
}

launch_player() {
    local -a args=("$@")
    local has_separator=0 arg
    if ((selected >= 0)); then
        for arg in "${args[@]}"; do [[ $arg == -- ]] && has_separator=1; done
        ((has_separator)) || args+=(--)
        exec "$player" "${args[@]}" --eawr-mod-root "${mod_paths[selected]}"
    fi
    exec "$player" "$@"
}

main() {
    local script_dir player selected=-1
    # Helpers share these caller-scoped outputs without subshells or array copies.
    local -a steam_libraries=() workshop_dirs=() local_mod_dirs=() mod_ids=() mod_paths=() mod_titles=()
    script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || return 1
    discover_player "$script_dir" || return 1
    discover_workshop_dirs || return 1
    discover_local_mod_dirs "$@"
    load_mods
    select_mod
    launch_player "$@"
}

main "$@"
