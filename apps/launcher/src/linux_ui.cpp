#include "linux_platform.hpp"

#include <gtk/gtk.h>
#include <iostream>

namespace eawr::launcher {
namespace {
class LinuxUi final : public Ui {
public:
    explicit LinuxUi(bool graphical) : graphical_(graphical) {}

    void show_error(const std::string& title, const std::string& message) override {
        std::cerr << title << ": " << message << '\n';
        if (!graphical_) return;
        GtkWidget* dialog = gtk_message_dialog_new(nullptr, GTK_DIALOG_MODAL, GTK_MESSAGE_ERROR,
                                                   GTK_BUTTONS_CLOSE, "%s", message.c_str());
        gtk_window_set_title(GTK_WINDOW(dialog), title.c_str());
        gtk_dialog_run(GTK_DIALOG(dialog));
        gtk_widget_destroy(dialog);
    }

    Pick pick_install(const std::vector<Install>& installs, const std::string& heading) override {
        if (!graphical_) {
            std::cerr << heading << "\nUse --game-root <folder> or run with a graphical display.\n";
            return {};
        }
        GtkWidget* dialog = gtk_dialog_new_with_buttons("Empire at War Revised", nullptr, GTK_DIALOG_MODAL,
            "Cancel", GTK_RESPONSE_CANCEL, "Browse…", GTK_RESPONSE_APPLY, "Use selected", GTK_RESPONSE_ACCEPT, nullptr);
        gtk_window_set_default_size(GTK_WINDOW(dialog), 680, 260);
        GtkWidget* content = gtk_dialog_get_content_area(GTK_DIALOG(dialog));
        gtk_container_set_border_width(GTK_CONTAINER(content), 16);
        GtkWidget* label = gtk_label_new(heading.c_str());
        gtk_label_set_line_wrap(GTK_LABEL(label), TRUE);
        gtk_box_pack_start(GTK_BOX(content), label, FALSE, FALSE, 8);
        GtkWidget* combo = gtk_combo_box_text_new();
        for (const auto& install : installs) {
            const auto text = install.source + ": " + path_to_utf8(install.root);
            gtk_combo_box_text_append_text(GTK_COMBO_BOX_TEXT(combo), text.c_str());
        }
        if (!installs.empty()) gtk_combo_box_set_active(GTK_COMBO_BOX(combo), 0);
        gtk_box_pack_start(GTK_BOX(content), combo, FALSE, FALSE, 8);
        gtk_dialog_set_response_sensitive(GTK_DIALOG(dialog), GTK_RESPONSE_ACCEPT, !installs.empty());
        gtk_widget_show_all(dialog);
        const int response = gtk_dialog_run(GTK_DIALOG(dialog));
        const int selected = gtk_combo_box_get_active(GTK_COMBO_BOX(combo));
        gtk_widget_destroy(dialog);
        if (response == GTK_RESPONSE_APPLY) return {Pick::Kind::browse, 0};
        if (response == GTK_RESPONSE_ACCEPT && selected >= 0) return {Pick::Kind::install, static_cast<std::size_t>(selected)};
        return {};
    }

    std::optional<fs::path> browse_for_folder(const std::string& prompt) override {
        if (!graphical_) return {};
        GtkFileChooserNative* dialog = gtk_file_chooser_native_new(prompt.c_str(), nullptr,
            GTK_FILE_CHOOSER_ACTION_SELECT_FOLDER, "Select", "Cancel");
        std::optional<fs::path> result;
        if (gtk_native_dialog_run(GTK_NATIVE_DIALOG(dialog)) == GTK_RESPONSE_ACCEPT) {
            char* path = gtk_file_chooser_get_filename(GTK_FILE_CHOOSER(dialog));
            if (path) result = fs::path(path);
            g_free(path);
        }
        g_object_unref(dialog);
        return result;
    }
private:
    bool graphical_;
};
}
std::unique_ptr<Ui> make_linux_ui(bool headless) {
    return std::make_unique<LinuxUi>(!headless && gtk_init_check(nullptr, nullptr));
}
}
