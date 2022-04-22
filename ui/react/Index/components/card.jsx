import React from 'react';
import PropTypes from 'prop-types';
import axios from 'axios';

import Grid from '@mui/material/Grid';
import Card from '@mui/material/Card';
import Button from '@mui/material/Button';
import Divider from '@mui/material/Divider';
import CheckIcon from '@mui/icons-material/Check';
import CloseIcon from '@mui/icons-material/Close';
import IconButton from '@mui/material/IconButton';
import CardHeader from '@mui/material/CardHeader';
import Typography from '@mui/material/Typography';
import CardActions from '@mui/material/CardActions';
import CardContent from '@mui/material/CardContent';
import CircularProgress from '@mui/material/CircularProgress';

import { TagDialog } from './tag';
import { DataList } from './data';
import { RSSIGraph } from './rssi';
import { parseDate } from '../../common/util';

export const MacInteraction = ({
  close,
  selectMac,
  selectedMac
}) => {
  const state = selectedMac.deviceData?.Connected ? 'Disconnect' : 'Connect';
  const [conText, setConText] = React.useState(state);
  const [loading, setLoading] = React.useState(false);
  const [statusFlash, setStatusFlash] = React.useState(false);
  const [tagOpen, setTagOpen] = React.useState(false);
  const { vendor, category } = selectedMac.ouid;

  const connectDisconnect = React.useRef((addr, choice) => {
    const otherChoice = (choice === 'connect') ? 'disconnect' : 'connect';
    clearInterval(window.macTicker);
    setLoading(true);
    setConText(`${choice}ing`);
    axios.get(`/api/${addr}/${choice}`).then((res) => {
      if (res.data.success) {
        setConText(`${choice}ed`);
        setStatusFlash('success');
        setTimeout(() => {
          setConText('Loading');
          setStatusFlash(false);
          selectMac({ y: addr }, () => {
            setLoading(false);
            setConText(otherChoice);
          });
        }, 3000);
      }
    }).catch((err) => {
      setStatusFlash('err');
      setConText(state);
      setTimeout(() => setStatusFlash(false), 2000);
      setLoading(false);
      selectMac({ y: addr });
    });
  }).current;

  const deleteSelectedMac = () => {
    const prompt = `Are you sure you want to delete ${selectedMac.addr}? All data will be lost.`

    if (confirm(prompt)) {
      clearInterval(window.macTicker);
      const promise = axios.delete(`/api/query/${selectedMac.addr}`);

      promise.then((res) => {
        if (res.data.success) {
          close();
        }
      }).catch((err) => {
        console.error(err);
      })
    }
  }
  
  const Title = () => (
    <>
      {category?.icon && (
        <span>{category.icon}</span>
      )}
      {selectedMac.addr}
    </>
  );

  const SubHeader = () => (
    <>
      Device: {selectedMac.name}
      {selectedMac.tag && (
        <>
          <br/>
          Tag: {selectedMac.tag}
        </>
      )}
    </>
  );

  let connectLoadingEndIcon = loading && <CircularProgress size={10} />;
  if (statusFlash) {
    connectLoadingEndIcon = (statusFlash === 'success' ? <CheckIcon size={10} /> : <CloseIcon size={10} color='secondary' />);
  }

  const buttonText = !loading
    ? state
    : conText

  return (
    <Grid item xs={12}>
      <Grid container spacing={2}>

        <Grid item xs={12}>
          <Card>
            <CardHeader
              action={
                <IconButton onClick={close}>
                  <CloseIcon />
                </IconButton>
              }
              title={<Title />}
              subheader={<SubHeader />}
            />
            <CardContent>
              {vendor && (
                <>
                  <Typography gutterBottom>
                    {vendor}
                    <br/>
                    {category && (
                      <>
                        {category.name}
                        {' '}
                        {category.description}
                        <br/>
                      </>
                    )}
                  </Typography>
                  <Divider sx={{ mb: 2, mt: 2 }} />
                </>
              )}
              <Typography gutterBottom>
                First Seen: {parseDate(selectedMac.first_seen).toLocaleString()}
                <br/>
                Last Seen: {parseDate(selectedMac.last_seen).toLocaleString()}
                <br/>
                {selectedMac.rssi.length > 0 && (
                  <>
                    {'RSSI: '}
                    {selectedMac.rssi[selectedMac.rssi.length -1]?.rssi}
                    <br/>
                  </>
                )}
                Seen: {selectedMac.seen}
                <br/>
                Data: {selectedMac.data.length}
                <br/>
                UUIDS: {selectedMac.uuid.length}
              </Typography>

              {selectedMac.uuid.length > 0 && (
                <>
                  <Divider sx={{ mb: 2, mt: 2 }}/>
                  <Typography gutterBottom>
                    {selectedMac.uuid.map((uuid,i) => (                      
                      <span style={{ display: 'block' }} key={uuid.uuid}>
                        <strong>{uuid.uuid}</strong> {uuid.lookup?.attribute}
                      </span>
                    ))}
                  </Typography>
                </>
              )}
            </CardContent>
            <CardActions disableSpacing sx={{ justifyContent: 'space-between' }}>
              <Button
                size='small'
                onClick={() => setTagOpen(true)}
              >
                Tag
              </Button>

              {selectedMac.deviceData && (
                <Button
                  size='small'
                  disabled={loading}
                  endIcon={connectLoadingEndIcon}
                  onClick={() => connectDisconnect(selectedMac.addr, selectedMac.deviceData?.Connected ? 'disconnect' : 'connect')}
                >
                  {buttonText}
                </Button>
              )}

              <Button
                size='small'
                onClick={() => deleteSelectedMac()}
              >
                Delete
              </Button>
            </CardActions>
          </Card>
        </Grid>

        {selectedMac.rssi.length > 1 && (
          <Grid item xs={12}>
            <Card sx={{ mh: 300 }}>
              <CardContent>
                <RSSIGraph rssi={selectedMac.rssi} />
              </CardContent>
            </Card>
          </Grid>
        )}

        {selectedMac.data.length > 0 && (
          <Grid item xs={12}>
            <Card>
              <CardContent>
                <DataList data={selectedMac.data} />
              </CardContent>
            </Card>
          </Grid>
        )}

        <TagDialog
          mac={selectedMac.addr}
          open={tagOpen}
          handleClose={() => setTagOpen(false)}
        />
      </Grid>
    </Grid>
  );
};

MacInteraction.propTypes = {
  // close function
  close: PropTypes.func,
  selectMac: PropTypes.func,
  selectedMac: PropTypes.oneOfType([
    PropTypes.bool,
    PropTypes.object,
  ]),
};

export default MacInteraction;
