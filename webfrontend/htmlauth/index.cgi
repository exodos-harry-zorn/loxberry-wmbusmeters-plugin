#!/usr/bin/perl -w

# LoxBerry Plugin wM-Bus Heat Meter Bridge - Perl Wrapper
#
# This script serves as the main entry point for the plugin's web interface.
# It uses the LoxBerry Web SDK to render the standard LoxBerry header, footer,
# and navigation, and then calls the Python content script to get the actual
# plugin content.

use CGI;
use LoxBerry::System;
use LoxBerry::Web;
use LoxBerry::Log;
use LoxBerry::JSON;
use warnings;
use strict;
use Data::Dumper;
use File::Basename;

##########################################################################
# Variables
##########################################################################

our $template;
our $lbplogdir = $ENV{'LBPLOGDIR'};
our $lbphtmldir = $ENV{'LBPHTMLDIR'};

my $pluginlogfile = "wmbusmetersbridge_ui.log";
our $log = LoxBerry::Log->new (
    name => 'wmbusmetersbridge_ui',
    filename => $lbplogdir ."/". $pluginlogfile,
    append => 1,
    addtime => 1
);

our $plugin = LoxBerry::System::plugindata();
our $q = CGI->new;

# Read the active tab from the query string (e.g., ?tab=overview)
my $active_tab = $q->param('tab') || 'overview';

# Define the tabs with their IDs, display names, and Font Awesome icons
my @TABS = (
    { id => "overview", name => "Overview", icon => "fa fa-tachometer-alt" },
    { id => "mqtt", name => "MQTT", icon => "fa fa-wifi" },
    { id => "radio", name => "Radio", icon => "fa fa-broadcast-tower" },
    { id => "meters", name => "Meters", icon => "fa fa-thermometer-half" },
    { id => "discovery", name => "Discovery & Logs", icon => "fa fa-search" },
);

##########################################################################
# Main program
##########################################################################

LOGSTART "wmbusmetersbridge UI";

# Pass the active tab to the Python script via environment variable
$ENV{'LB_ACTIVE_TAB'} = $active_tab;

# Set environment variables for Python script to access LoxBerry paths
$ENV{'LBP_CONFIGDIR'} = $ENV{'LBPCONFIGDIR'};
$ENV{'LBP_BINDIR'} = $ENV{'LBPBINDIR'};
$ENV{'LBP_TMPDIR'} = $ENV{'LBPTMPDIR'};

# content.py is now in the same directory as this index.cgi
my $content_script_path = dirname($0) . "/content.py";
my $content_output = '';
my $python_exit_code = 0;

if ($ENV{'REQUEST_METHOD'} && $ENV{'REQUEST_METHOD'} eq 'POST') {
    my $post_data = "";
    if (defined $ENV{'CONTENT_LENGTH'} && $ENV{'CONTENT_LENGTH'} > 0) {
        read(STDIN, $post_data, $ENV{'CONTENT_LENGTH'});
    }
    
    # Execute Python script and pipe POST data to its STDIN
    open(my $pipe, '|-', "/usr/bin/env python3 $content_script_path > /tmp/wmbusmetersbridge_content.out 2>&1") or do {
        LOGCRIT "Cannot open pipe to Python content script: $!";
        die "Cannot open pipe to Python content script: $!";
    };
    print $pipe $post_data;
    close($pipe);
    $python_exit_code = $? >> 8;
    
    if (-e "/tmp/wmbusmetersbridge_content.out") {
        $content_output = `cat /tmp/wmbusmetersbridge_content.out`;
    }
} else {
    # For GET requests, simply execute Python script
    $content_output = LoxBerry::System::readpipe("/usr/bin/env python3 $content_script_path 2>&1");
    $python_exit_code = $? >> 8;
}

# Check for errors from the Python script
if ($python_exit_code != 0) {
    LOGERR "Python content script exited with code $python_exit_code: $content_output";
    $content_output = "<div class=\"ui-corner-all ui-shadow ui-bar-a ui-bar-red\" style=\"margin-bottom: 1em;\">\n" .
                      "<p><b>ERROR:</b> An error occurred in the Python content script.</p>\n" .
                      "<pre>$content_output</pre>\n" .
                      "</div>";
}

# Determine the plugin title and version from plugin.cfg
my $plugin_title = $plugin->{PLUGINDB_TITLE} || "wM-Bus Heat Meter Bridge";
my $plugin_version = $plugin->{PLUGINDB_VERSION} || "unknown";
my $helplink = $plugin->{PLUGINDB_HELPLINK} || "https://github.com/exodos-harry-zorn/loxberry-wmbusmeters-plugin"; # Fallback help link

# Render the LoxBerry header
LoxBerry::Web::lbheader($plugin_title . " v" . $plugin_version, $helplink, "");

# Render notifications (if any)
print LoxBerry::Log::get_notifications_html($plugin->{PLUGINDB_FOLDER});

# Render the navigation tabs manually to control icons and active state
# This assumes LoxBerry's CSS for navbar and icons is available
print "<div data-role=\"navbar\"><ul>\n";
foreach my $tab (@TABS) {
    my $tab_id = $tab->{id};
    my $tab_name = $tab->{name};
    my $tab_icon = $tab->{icon};
    my $active_class = ($tab_id eq $active_tab) ? "ui-btn-active ui-state-persist" : "";
    print "<li><a href=\"?tab=$tab_id\" class=\"ui-btn ui-btn-inline ui-corner-all ui-shadow $active_class\"><i class=\"$tab_icon\"></i> $tab_name</a></li>\n";
}
print "</ul></div>\n";

# Print the content from the Python script
print "<div style=\"margin-top: 1em;\">\n";
print $content_output;
print "</div>\n";

# Render the LoxBerry footer
LoxBerry::Web::lbfooter();

LOGEND "wmbusmetersbridge UI";

exit;
